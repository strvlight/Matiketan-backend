import base64
from django import forms
from django.contrib import admin
from django.utils.html import format_html
from .models import (
    Enforcer, Violation, Ticket, Detection, DetectionImage,
    DeclineEvidence, VehicleRegistration, Evidence,
    DeclinedDetection, ConfirmedDetection, InProgressDetection,
)
from .views import compress_image

class EnforcerAdminForm(forms.ModelForm):
    photo = forms.ImageField(required=False, label='Profile photo (upload)')
    remove_photo = forms.BooleanField(required=False, label='Remove current photo')

    class Meta:
        model = Enforcer
        exclude = ('profile_photo',)
@admin.register(Enforcer)
class EnforcerAdmin(admin.ModelAdmin):
    form = EnforcerAdminForm
    list_display = ('full_name', 'badge_id', 'assigned_area', 'is_on_duty')
    search_fields = ('full_name', 'badge_id', 'assigned_area')
    readonly_fields = ('photo_preview',)

    def photo_preview(self, obj):
        if not obj or not obj.photo_data:
            return '-'
        return _img_tag(obj.photo_data, obj.photo_type, width=160)
    photo_preview.short_description = 'Current photo'

    def save_model(self, request, obj, form, change):
        f = form.cleaned_data.get('photo')
        if f:
            obj.photo_data = compress_image(f, max_width=400, quality=80)
            obj.photo_type = 'image/jpeg'
        elif form.cleaned_data.get('remove_photo'):
            obj.photo_data = None
        super().save_model(request, obj, form, change)
        
        
admin.site.register(Violation)
admin.site.register(Ticket)
admin.site.register(VehicleRegistration)
admin.site.register(Evidence)


def _img_tag(data, content_type, width=320):
    """Render binary image data inline (images are stored in the database)."""
    if not data:
        return '-'
    b64 = base64.b64encode(bytes(data)).decode()
    return format_html(
        '<img src="data:{};base64,{}" style="max-width:{}px;border-radius:6px;" />',
        content_type, b64, width,
    )


class DeclineEvidenceInline(admin.TabularInline):
    model = DeclineEvidence
    extra = 0
    can_delete = False
    fields = ('preview', 'created_at')
    readonly_fields = ('preview', 'created_at')

    def preview(self, obj):
        return _img_tag(obj.data, obj.content_type)

    def has_add_permission(self, request, obj=None):
        return False


class DetectionAdminBase(admin.ModelAdmin):
    list_display = (
        'plate_number', 'status', 'location', 'confidence_score',
        'detected_at', 'claimed_by', 'reviewed_by', 'linked_ticket',
        'decline_reason', 'declined_at',
    )
    list_filter = ('detected_at', 'reviewed_by', 'claimed_by')
    search_fields = ('plate_number', 'location', 'decline_reason', 'decline_notes')
    date_hierarchy = 'detected_at'
    readonly_fields = ('detection_photo', 'declined_at', 'claimed_at', 'last_seen')
    inlines = [DeclineEvidenceInline]
    status_filter = None

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(status=self.status_filter) if self.status_filter else qs

    def detection_photo(self, obj):
        img = DetectionImage.objects.filter(detection=obj).first()
        return _img_tag(img.data, img.content_type) if img else '-'
    detection_photo.short_description = 'Detection photo'


@admin.register(Detection)
class DetectionAdmin(DetectionAdminBase):
    list_filter = ('status',) + DetectionAdminBase.list_filter


@admin.register(DeclinedDetection)
class DeclinedDetectionAdmin(DetectionAdminBase):
    status_filter = 'declined'


@admin.register(ConfirmedDetection)
class ConfirmedDetectionAdmin(DetectionAdminBase):
    status_filter = 'confirmed'


@admin.register(InProgressDetection)
class InProgressDetectionAdmin(DetectionAdminBase):
    status_filter = 'in_progress'