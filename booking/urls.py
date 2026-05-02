from django.urls import path
from .views import BookReservationView

urlpatterns = [
    path('book/', BookReservationView.as_view(), name='book-reservation'),
]
