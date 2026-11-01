from django.db import models
from django.contrib.auth.models import User

# ---------------------------
# ENFORCER (extends Django's built-in User)
# ---------------------------
class Enforcer(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    badge_id = models.CharField(max_length=20, unique=True)
    full_name = models.CharField(max_length=100)
    contact_number = models.CharField(max_length=15, blank=True)
    is_on_duty = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)
    
    profile_photo = models.ImageField(upload_to='enforcer_profiles/', blank=True, null=True)

    def __str__(self):
        return f"{self.full_name} ({self.badge_id})"


# ---------------------------
# VIOLATION TYPE (e.g. No Parking, Obstruction) 
# ---------------------------
class Violation(models.Model):
    name = models.CharField(max_length=100)
    fine_amount = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


# ---------------------------
# TICKET (the actual issued ticket/apprehension)
# ---------------------------
class Ticket(models.Model):
    STATUS_CHOICES = [
        ('unpaid', 'Unpaid'),
        ('paid', 'Paid'),
    ]

    transaction_code = models.CharField(max_length=20, unique=True)
    plate_number = models.CharField(max_length=15)
    vehicle_type = models.CharField(max_length=50, blank=True)
    driver_name = models.CharField(max_length=100, blank=True, default='')
    violation = models.ForeignKey(Violation, on_delete=models.SET_NULL, null=True)
    location = models.CharField(max_length=255)
    enforcer = models.ForeignKey(Enforcer, on_delete=models.SET_NULL, null=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='unpaid')
    date_issued = models.DateTimeField(auto_now_add=True)
    date_paid = models.DateTimeField(null=True, blank=True) 
    photo = models.ImageField(upload_to='ticket_photos/', blank=True, null=True)

    def __str__(self):
        return f"{self.transaction_code} - {self.plate_number}"


# ---------------------------
# DETECTION (from your YOLOv5 + ALPR system)
# ---------------------------
class Detection(models.Model):
    STATUS_CHOICES = [
         ('monitoring', 'Monitoring'),
        ('pending', 'Pending Review'),
        ('confirmed', 'Confirmed'),
        ('declined', 'Declined (False Positive)'),
    ]

    plate_number = models.CharField(max_length=15)
    location = models.CharField(max_length=255)
    confidence_score = models.DecimalField(max_digits=5, decimal_places=2)  # e.g. 92.50%
    detected_photo = models.ImageField(upload_to='detections/', blank=True, null=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    detected_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(Enforcer, on_delete=models.SET_NULL, null=True, blank=True)
    linked_ticket = models.ForeignKey(Ticket, on_delete=models.SET_NULL, null=True, blank=True)
    decline_reason = models.CharField(max_length=255, blank=True, default='')
    decline_notes = models.TextField(blank=True, default='')
    declined_at = models.DateTimeField(null=True, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    sighting_count = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"Detection - {self.plate_number} ({self.status})"
    
class DetectionImage(models.Model):
    detection = models.OneToOneField(
        Detection, on_delete=models.CASCADE, related_name='image'
    )
    data = models.BinaryField()
    content_type = models.CharField(max_length=50, default='image/jpeg')
    created_at = models.DateTimeField(auto_now_add=True)
    
class DeclineEvidence(models.Model):
    detection = models.ForeignKey(
        Detection, on_delete=models.CASCADE, related_name='decline_evidence'
    )
    data = models.BinaryField()
    content_type = models.CharField(max_length=50, default='image/jpeg')
    created_at = models.DateTimeField(auto_now_add=True)

# Create your models here.

class VehicleRegistration(models.Model):
    plate_number = models.CharField(max_length=20, unique=True)
    owner_name   = models.CharField(max_length=100)
    owner_contact = models.CharField(max_length=20, blank=True)
    vehicle_type = models.CharField(max_length=100, blank=True)
    brand        = models.CharField(max_length=50, blank=True)
    color        = models.CharField(max_length=30, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.plate_number
    

class DeviceToken(models.Model):
    user = models.ForeignKey(
        'auth.User', on_delete=models.CASCADE, related_name='device_tokens'
    )
    token = models.CharField(max_length=300, unique=True)
    updated_at = models.DateTimeField(auto_now=True)

    
## ─────────────────────────────────────────
# EVIDENCE (photos/videos per ticket)
# ─────────────────────────────────────────
class Evidence(models.Model):
    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name='evidence'
    )
    file = models.FileField(upload_to='evidence/')
    file_type = models.CharField(max_length=10)  # 'photo' or 'video'
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Evidence for {self.ticket.transaction_code}"