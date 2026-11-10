import base64
from django.contrib import admin
from django.utils.html import format_html
from .models import (
    Enforcer, Violation, Ticket, Detection, DetectionImage,
    DeclineEvidence, VehicleRegistration, Evidence,
)

admin.site.register(Enforcer)
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


@admin.register(Detection)
class DetectionAdmin(admin.ModelAdmin):
    list_display = (
        'plate_number', 'status', 'location', 'confidence_score',
        'detected_at', 'claimed_by', 'reviewed_by', 'linked_ticket',
        'decline_reason', 'declined_at', 'sighting_count',
    )
    list_filter = ('status', 'detected_at', 'declined_at', 'reviewed_by', 'claimed_by')
    search_fields = ('plate_number', 'location', 'decline_reason', 'decline_notes')
    date_hierarchy = 'detected_at'
    readonly_fields = (
        'detection_photo', 'declined_at', 'claimed_at', 'last_seen',
    )
    inlines = [DeclineEvidenceInline]
    actions = ['show_declined_hint']

    def detection_photo(self, obj):
        img = DetectionImage.objects.filter(detection=obj).first()
        return _img_tag(img.data, img.content_type) if img else '-'
    detection_photo.short_description = 'Detection photo'

    @admin.action(description='(Tip) Use the Status filter on the right')
    def show_declined_hint(self, request, queryset):
        pass