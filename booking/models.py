from django.db import models


class BookingLog(models.Model):
    """Append-only audit log of every booking attempt."""

    class Status(models.TextChoices):
        SUCCESS = 'success'
        FAILURE = 'failure'

    venue_id = models.IntegerField()
    date = models.DateField()
    party_size = models.IntegerField()
    requested_time = models.TimeField()
    time_delta_minutes = models.IntegerField(null=True)

    # Populated on success
    booked_time = models.TimeField(null=True)
    reservation_id = models.CharField(max_length=128, blank=True)
    resy_token = models.CharField(max_length=512, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices)
    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.status}] venue={self.venue_id} {self.date} {self.requested_time}"
