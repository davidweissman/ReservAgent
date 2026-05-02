from django.urls import path

from .agent_views import AuthTestLoginView, NaturalBookView, ParseIntentView, TestSearchView
from .views import BookReservationView

urlpatterns = [
    path('book/', BookReservationView.as_view(), name='book-reservation'),
    path('natural-booking/', ParseIntentView.as_view(), name='natural-booking'),
    path('parse-intent/', ParseIntentView.as_view(), name='parse-intent'),  # legacy alias
    path('test/search/', TestSearchView.as_view(), name='test-search'),
    path('natural-book/', NaturalBookView.as_view(), name='natural-book'),
    path('auth/test-login/', AuthTestLoginView.as_view(), name='auth-test-login'),
]
