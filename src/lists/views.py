import csv
import logging

from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Count, F, OuterRef, Q, Subquery
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_POST

from app import helpers
from app.models import (
    IGDBGameTaxonomy,
    IGDBGameTaxonomyAssignment,
    Item,
    MediaManager,
    MediaTypes,
    Sources,
    TMDBMovieTaxonomy,
    TMDBMovieTaxonomyAssignment,
    tmdb_metadata_expiry_cutoff,
)
from app.providers import services
from lists.forms import CustomListForm
from lists.models import CustomList, CustomListItem
from users.models import ListDetailSortChoices, ListSortChoices, MediaStatusChoices

logger = logging.getLogger(__name__)

LIST_TAXONOMY_FACETS = {
    "igdb": {
        "model": IGDBGameTaxonomy,
        "assignment": IGDBGameTaxonomyAssignment,
        "type": MediaTypes.GAME.value,
        "source": Sources.IGDB.value,
        "option_lookup": "game_assignments__metadata__item__custom_lists",
        "kinds": {
            "genre": IGDBGameTaxonomy.Kind.GENRE,
            "theme": IGDBGameTaxonomy.Kind.THEME,
            "keyword": IGDBGameTaxonomy.Kind.KEYWORD,
        },
    },
    "tmdb": {
        "model": TMDBMovieTaxonomy,
        "assignment": TMDBMovieTaxonomyAssignment,
        "type": MediaTypes.MOVIE.value,
        "source": Sources.TMDB.value,
        "option_lookup": "movie_assignments__metadata__item__custom_lists",
        "kinds": {
            "genre": TMDBMovieTaxonomy.Kind.GENRE,
            "keyword": TMDBMovieTaxonomy.Kind.KEYWORD,
        },
    },
}


def get_list_taxonomy_filters(query_params):
    """Parse provider-qualified taxonomy selections for a custom list."""
    filters = {}
    for provider, config in LIST_TAXONOMY_FACETS.items():
        filters[provider] = {}
        for facet in config["kinds"]:
            parameter = f"{provider}_{facet}"
            include = _parse_taxonomy_ids(query_params.getlist(parameter))
            exclude = _parse_taxonomy_ids(query_params.getlist(f"{parameter}_exclude"))
            filters[provider][facet] = {"include": include, "exclude": exclude}
    return filters


def _parse_taxonomy_ids(values):
    """Parse unique positive provider taxonomy IDs."""
    result = []
    for value in values:
        try:
            provider_id = int(value)
        except (TypeError, ValueError):
            continue
        if provider_id > 0 and provider_id not in result:
            result.append(provider_id)
    return result


def get_list_taxonomy_options(custom_list, current_filters):
    """Return taxonomy values actually represented in this list."""
    result = {}
    for provider, config in LIST_TAXONOMY_FACETS.items():
        result[provider] = {}
        for facet, value in config["kinds"].items():
            queryset = config["model"].objects.filter(
                kind=value,
                **{config["option_lookup"]: custom_list},
            )
            if provider == "tmdb":
                queryset = queryset.filter(
                    movie_assignments__metadata__synced_at__gte=tmdb_metadata_expiry_cutoff(),
                )
            selections = current_filters[provider][facet]
            result[provider][facet] = [
                {
                    "provider_id": taxonomy.provider_id,
                    "name": taxonomy.name,
                    "state": (
                        "include"
                        if taxonomy.provider_id in selections["include"]
                        else "exclude"
                        if taxonomy.provider_id in selections["exclude"]
                        else "neutral"
                    ),
                }
                for taxonomy in queryset.distinct().order_by("name")
            ]
    return result


def apply_list_taxonomy_filters(items, custom_list, filters):
    """Apply each provider's facets to its own media type, retaining other types."""
    for provider, config in LIST_TAXONOMY_FACETS.items():
        provider_filters = filters[provider]
        if not any(
            value["include"] or value["exclude"] for value in provider_filters.values()
        ):
            continue
        matching_items = Item.objects.filter(
            custom_lists=custom_list,
            media_type=config["type"],
            source=config["source"],
        )
        for facet, selection in provider_filters.items():
            kind = config["kinds"][facet]
            assignment_ids = config["assignment"].objects.filter(
                metadata__item__custom_lists=custom_list,
                taxonomy__kind=kind,
            )
            if provider == "tmdb":
                assignment_ids = assignment_ids.filter(
                    metadata__synced_at__gte=tmdb_metadata_expiry_cutoff(),
                )
            if selection["include"]:
                matching_items = matching_items.filter(
                    pk__in=assignment_ids.filter(
                        taxonomy__provider_id__in=selection["include"],
                    ).values("metadata__item_id"),
                )
            if selection["exclude"]:
                matching_items = matching_items.exclude(
                    pk__in=assignment_ids.filter(
                        taxonomy__provider_id__in=selection["exclude"],
                    ).values("metadata__item_id"),
                )
        items = items.filter(~Q(media_type=config["type"]) | Q(pk__in=matching_items))
    return items


@require_GET
def lists(request):
    """Return the custom list page."""
    # Get parameters from request
    search_query = request.GET.get("q", "")
    page = request.GET.get("page", 1)
    sort_by = request.user.update_preference("lists_sort", request.GET.get("sort"))

    custom_lists = CustomList.objects.get_user_lists(request.user)

    if search_query:
        custom_lists = custom_lists.filter(
            Q(name__icontains=search_query) | Q(description__icontains=search_query),
        )

    if sort_by == "name":
        custom_lists = custom_lists.order_by("name")
    elif sort_by == "items_count":
        custom_lists = custom_lists.annotate(
            items_count=Count("items", distinct=True),
        ).order_by("-items_count")
    elif sort_by == "newest_first":
        custom_lists = custom_lists.order_by("-id")
    else:  # last_item_added is the default
        # Get the latest update date for each list
        custom_lists = custom_lists.annotate(
            latest_update=Subquery(
                CustomListItem.objects.filter(
                    custom_list=OuterRef("pk"),
                )
                .order_by("-date_added")
                .values("date_added")[:1],
            ),
        ).order_by("-latest_update", "name")

    items_per_page = 20
    paginator = Paginator(custom_lists, items_per_page)
    lists_page = paginator.get_page(page)

    # Create a form for each list
    # needs unique id for django-select2
    for i, custom_list in enumerate(lists_page, start=1):
        custom_list.form = CustomListForm(
            instance=custom_list,
            auto_id=f"id_{i}_%s",
        )

    if request.headers.get("HX-Request"):
        return render(
            request,
            "lists/components/list_grid.html",
            {
                "custom_lists": lists_page,
            },
        )

    create_list_form = CustomListForm()

    return render(
        request,
        "lists/custom_lists.html",
        {
            "custom_lists": lists_page,
            "form": create_list_form,
            "current_sort": sort_by,
            "sort_choices": ListSortChoices.choices,
        },
    )


@require_GET
def list_detail(request, list_id):
    """Return the detail page of a custom list."""
    custom_list = get_object_or_404(
        CustomList.objects.select_related("owner").prefetch_related("collaborators"),
        id=list_id,
    )

    if not custom_list.user_can_view(request.user):
        msg = "List not found"
        raise Http404(msg)

    # Get and process request parameters
    params = {
        "sort_by": request.user.update_preference(
            "list_detail_sort",
            request.GET.get("sort"),
        ),
        "media_types": request.GET.getlist("types"),
        "status_filter": request.user.update_preference(
            "list_detail_status",
            request.GET.get("status"),
        ),
        "page": int(request.GET.get("page", 1)),
        "search_query": request.GET.get("q", ""),
    }

    # Build and filter base queryset
    items = custom_list.items.all()
    if params["search_query"]:
        items = items.filter(title__icontains=params["search_query"])
    available_media_types = list(
        items.order_by().values_list("media_type", flat=True).distinct(),
    )
    if request.GET.get("types_selected"):
        params["media_types"] = [
            media_type
            for media_type in params["media_types"]
            if media_type in available_media_types
        ]
        items = items.filter(media_type__in=params["media_types"])
    elif request.GET.get("type") and request.GET["type"] != "all":
        params["media_types"] = [request.GET["type"]]
        items = items.filter(media_type=request.GET["type"])
    else:
        params["media_types"] = available_media_types

    taxonomy_filters = get_list_taxonomy_filters(request.GET)
    taxonomy_options = get_list_taxonomy_options(custom_list, taxonomy_filters)
    items = apply_list_taxonomy_filters(items, custom_list, taxonomy_filters)

    # Get distinct media types for filtering
    media_types = items.values_list("media_type", flat=True).distinct()
    media_manager = MediaManager()
    media_by_item_id = {}

    # Filter by status if specified
    if params["status_filter"] != MediaStatusChoices.ALL:
        item_ids = items.values_list("id", flat=True)
        media_by_item_id = media_manager.fetch_media_for_items(
            media_types,
            item_ids,
            request.user,
            status_filter=params["status_filter"],
        )
        # Filter items to only those with the specified status
        items = items.filter(id__in=media_by_item_id.keys())

    # Apply sorting
    sort_mapping = {
        "date_added": ["-customlistitem__date_added"],
        "title": [
            F("title").asc(nulls_last=True),
            F("season_number").asc(nulls_first=True),
            F("episode_number").asc(nulls_first=True),
        ],
        "media_type": ["media_type"],
    }
    items = items.order_by(
        *sort_mapping.get(params["sort_by"], ["-customlistitem__date_added"]),
    )

    # Paginate
    paginator = Paginator(items, 16)
    items_page = paginator.get_page(params["page"])

    # If no status filter was applied, fetch media objects for paginated items only
    if params["status_filter"] == MediaStatusChoices.ALL:
        media_types_in_page = {item.media_type for item in items_page}
        page_item_ids = [item.id for item in items_page]
        media_by_item_id = media_manager.fetch_media_for_items(
            media_types_in_page,
            page_item_ids,
            request.user,
        )

    # Annotate items with media objects
    for item in items_page:
        item.media = media_by_item_id.get(item.id)

    # Base context for both full and partial responses
    context = {
        "custom_list": custom_list,
        "items": items_page,
        "has_next": items_page.has_next(),
        "next_page_number": items_page.next_page_number()
        if items_page.has_next()
        else None,
        "current_sort": params["sort_by"],
        "current_status": params["status_filter"] or MediaStatusChoices.ALL,
        "sort_choices": ListDetailSortChoices.choices,
        "status_choices": MediaStatusChoices.choices,
        "available_media_types": available_media_types,
        "current_media_types": params["media_types"],
        "taxonomy_filters": taxonomy_filters,
        "taxonomy_options": taxonomy_options,
        "taxonomy_groups": [
            {
                "provider": provider,
                "label": "Game filters" if provider == "igdb" else "Movie filters",
                "facets": [
                    {"name": facet, "options": values}
                    for facet, values in provider_options.items()
                ],
            }
            for provider, provider_options in taxonomy_options.items()
            if any(provider_options.values())
        ],
        "has_taxonomy_options": any(
            option_list
            for provider_options in taxonomy_options.values()
            for option_list in provider_options.values()
        ),
    }

    # Additional context for full page render. Soft-navigation body swaps (e.g.
    # after saving from an edit modal) also need the full page, not the partial.
    if not request.headers.get("HX-Request") or request.headers.get(
        "X-Soft-Navigation"
    ):
        context.update(
            {
                "form": CustomListForm(instance=custom_list),
                "media_types": MediaTypes.values,
                "items_count": paginator.count,
                "collaborators_count": custom_list.collaborators.count() + 1,
            },
        )
        return render(request, "lists/list_detail.html", context)

    # HTMX partial response
    return render(request, "lists/components/media_grid.html", context)


@require_GET
def list_export_csv(request, list_id):
    """Export the filtered movie, game, and TV entries from a custom list."""
    custom_list = get_object_or_404(
        CustomList.objects.select_related("owner").prefetch_related("collaborators"),
        id=list_id,
    )
    if not custom_list.user_can_view(request.user):
        msg = "List not found"
        raise Http404(msg)

    items = custom_list.items.all()
    query = request.GET.get("q", "")
    if query:
        items = items.filter(title__icontains=query)
    available_types = list(items.values_list("media_type", flat=True).distinct())
    if request.GET.get("types_selected"):
        types = [
            value for value in request.GET.getlist("types") if value in available_types
        ]
        items = items.filter(media_type__in=types)
    elif request.GET.get("type") and request.GET["type"] != "all":
        types = [request.GET["type"]]
        items = items.filter(media_type__in=types)
    else:
        types = available_types

    filters = get_list_taxonomy_filters(request.GET)
    items = apply_list_taxonomy_filters(items, custom_list, filters)
    items = items.filter(
        media_type__in=[
            MediaTypes.MOVIE.value,
            MediaTypes.TV.value,
            MediaTypes.GAME.value,
        ]
    )

    status = request.GET.get("status", MediaStatusChoices.ALL)
    if status != MediaStatusChoices.ALL:
        media_by_item_id = MediaManager().fetch_media_for_items(
            items.values_list("media_type", flat=True).distinct(),
            items.values_list("id", flat=True),
            request.user,
            status_filter=status,
        )
        items = items.filter(id__in=media_by_item_id)
    else:
        media_by_item_id = MediaManager().fetch_media_for_items(
            items.values_list("media_type", flat=True).distinct(),
            items.values_list("id", flat=True),
            request.user,
        )

    output = HttpResponse(content_type="text/csv; charset=utf-8")
    filename = f"{slugify(custom_list.name) or list_id}-media.csv"
    output["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(output)
    writer.writerow(
        [
            "title",
            "release_date",
            "creator",
            "original_title",
            "media_type",
            "status",
            "progress",
            "source",
            "media_id",
        ]
    )
    for item in (
        items.select_related().order_by("title", "media_type").iterator(chunk_size=500)
    ):
        track = media_by_item_id.get(item.id)
        metadata = cache.get(f"{item.source}_{item.media_type}_{item.media_id}") or {}
        details = metadata.get("details", {})
        date = details.get("release_date") or details.get("first_air_date") or ""
        creator = (
            details.get("director")
            or details.get("creators")
            or details.get("companies")
            or ""
        )
        if isinstance(creator, list):
            creator = ", ".join(
                value.get("name", "") if isinstance(value, dict) else str(value)
                for value in creator
            )
        writer.writerow(
            [
                item.title,
                date,
                creator,
                details.get("original_title", ""),
                item.media_type,
                getattr(track, "status", ""),
                getattr(track, "progress", ""),
                item.source,
                item.media_id,
            ]
        )
    return output


@require_POST
def create(request):
    """Create a new custom list."""
    form = CustomListForm(request.POST)
    if form.is_valid():
        custom_list = form.save(commit=False)
        custom_list.owner = request.user
        custom_list.save()
        form.save_m2m()
        logger.info("%s list created successfully.", custom_list)
    else:
        logger.error(form.errors.as_json())
        helpers.form_error_messages(form, request)
    return helpers.redirect_back(request)


@require_POST
def edit(request):
    """Edit an existing custom list."""
    list_id = request.POST.get("list_id")
    custom_list = get_object_or_404(CustomList, id=list_id)
    if custom_list.user_can_edit(request.user):
        form = CustomListForm(request.POST, instance=custom_list)
        if form.is_valid():
            form.save()
            logger.info("%s list edited successfully.", custom_list)
    else:
        messages.error(request, "You do not have permission to edit this list.")
    return helpers.redirect_back(request)


@require_POST
def delete(request):
    """Delete a custom list."""
    list_id = request.POST.get("list_id")
    custom_list = get_object_or_404(CustomList, id=list_id)
    if custom_list.user_can_delete(request.user):
        custom_list.delete()
        logger.info("%s list deleted successfully.", custom_list)
        return redirect("lists")

    messages.error(request, "You do not have permission to delete this list.")
    return helpers.redirect_back(request)


@require_GET
def lists_modal(
    request,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
):
    """Return the modal showing all custom lists and allowing to add to them."""
    try:
        item = Item.objects.get(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            episode_number=episode_number,
        )
    except Item.DoesNotExist:
        metadata = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
            episode_number,
        )
        item = Item.objects.create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            episode_number=episode_number,
            title=metadata["title"],
            image=metadata["image"],
        )

    custom_lists = CustomList.objects.get_user_lists_with_item(request.user, item)

    return render(
        request,
        "lists/components/fill_lists.html",
        {"item": item, "custom_lists": custom_lists},
    )


@require_POST
def list_item_toggle(request):
    """Add or remove an item from a custom list."""
    item_id = request.POST["item_id"]
    custom_list_id = request.POST["custom_list_id"]

    item = get_object_or_404(Item, id=item_id)
    custom_list = get_object_or_404(
        CustomList.objects.filter(
            Q(owner=request.user) | Q(collaborators=request.user),
            id=custom_list_id,
        ).distinct(),  # To prevent duplicates, when user is owner and collaborator
    )

    if custom_list.items.filter(id=item.id).exists():
        custom_list.items.remove(item)
        logger.info("%s removed from %s.", item, custom_list)
        has_item = False
    else:
        custom_list.items.add(item)
        logger.info("%s added to %s.", item, custom_list)
        has_item = True

    return render(
        request,
        "lists/components/list_item_button.html",
        {"custom_list": custom_list, "item": item, "has_item": has_item},
    )
