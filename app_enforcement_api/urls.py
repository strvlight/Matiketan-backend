from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (
    EnforcerViewSet, ViolationViewSet,
    TicketViewSet, DetectionViewSet,
    login_view, logout_view,
    submit_ticket, get_tickets,
    lookup_plate, upload_evidence,
    receive_detection, get_pending_detections,
    update_detection,
    admin_login, admin_search_tickets, admin_mark_paid,
    admin_create_enforcer
)

router = DefaultRouter()
router.register(r'enforcers', EnforcerViewSet)
router.register(r'violations', ViolationViewSet)
router.register(r'tickets', TicketViewSet)
router.register(r'detections', DetectionViewSet)


urlpatterns = [
    path('auth/login/', login_view, name='login'),
    path('auth/logout/', logout_view, name='logout'),
    path('submit-ticket/', submit_ticket, name='submit_ticket'),
    path('my-tickets/', get_tickets, name='get_tickets'),
    path('lookup-plate/', lookup_plate, name='lookup_plate'),
    path('tickets/<int:ticket_id>/evidence/', upload_evidence, name='upload_evidence'),
    path('detections/receive/', receive_detection, name='receive_detection'),
    path('detections/pending/', get_pending_detections, name='pending_detections'),
    path('detections/<int:detection_id>/update/', update_detection, name='update_detection'),
    path('auth/admin-login/', admin_login, name='admin_login'),
    path('admin/tickets/search/', admin_search_tickets, name='admin_search_tickets'),
    path('admin/tickets/<int:ticket_id>/pay/', admin_mark_paid, name='admin_mark_paid'),
    path('admin/enforcers/create/', admin_create_enforcer, name='admin_create_enforcer'),
] + router.urls