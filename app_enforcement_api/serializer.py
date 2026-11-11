from rest_framework import serializers
from .models import Enforcer, Violation, Ticket, Detection


class EnforcerSerializer(serializers.ModelSerializer):
    profile_photo_url = serializers.SerializerMethodField()

    class Meta:
        model = Enforcer
        fields = [
            'id', 'badge_id', 'full_name', 'contact_number',
            'is_on_duty', 'assigned_area', 'profile_photo', 'profile_photo_url',
        ]
        extra_kwargs = {
            'profile_photo': {'write_only': True, 'required': False},
        }

    def get_profile_photo_url(self, obj):
        if not obj.profile_photo:
            return None
        request = self.context.get('request')
        url = obj.profile_photo.url
        return request.build_absolute_uri(url) if request else url


class ViolationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Violation
        fields = '__all__'


class TicketSerializer(serializers.ModelSerializer):
    # Show the violation name instead of just the ID number
    violation_name = serializers.CharField(source='violation.name', read_only=True)
    enforcer_name = serializers.CharField(source='enforcer.full_name', read_only=True)

    class Meta:
        model = Ticket
        fields = '__all__'


class DetectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Detection
        fields = '__all__'
