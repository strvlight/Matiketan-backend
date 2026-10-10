from django.contrib import admin
from .models import Enforcer, Violation, Ticket, Detection, VehicleRegistration, Evidence

admin.site.register(Enforcer)
admin.site.register(Violation)
admin.site.register(Ticket)
admin.site.register(Detection)
admin.site.register(VehicleRegistration) 
admin.site.register(Evidence)  