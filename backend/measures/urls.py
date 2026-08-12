from django.urls import path

from . import views

urlpatterns = [
    path("detail/", views.measure_detail, name="measure-detail"),
    path("program-profile/", views.program_profile, name="program-profile"),
    path("portfolio-profile/", views.portfolio_profile, name="portfolio-profile"),
    path(
        "agency-outcome-profile/",
        views.agency_outcome_profile,
        name="agency-outcome-profile",
    ),
    path("text/", views.measure_text, name="measure-text"),
    path("list/", views.measure_list, name="measure-list"),
    path("by-id/", views.measure_by_id, name="measure-by-id"),
    path("search-text/", views.measure_text_search, name="measure-text-search"),
    path("search-topic/", views.measure_topic_search, name="measure-topic-search"),
]
