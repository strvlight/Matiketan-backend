from rest_framework import serializers
from .models import Enforcer, Violation, Ticket, Detection


class EnforcerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Enforcer
        fields = '__all__'


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