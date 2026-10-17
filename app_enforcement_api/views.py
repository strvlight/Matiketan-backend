from rest_framework import viewsets, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.authtoken.models import Token
from django.utils import timezone
from django.db.models import Q, Value
from django.db.models.functions import Replace
from django.db import transaction 
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
import random, string

from .models import Enforcer, Evidence, Violation, Ticket, Detection, VehicleRegistration
from .serializer import (
    EnforcerSerializer, ViolationSerializer,
    TicketSerializer, DetectionSerializer
)


# ─────────────────────────────────────────
# HELPER: Generate transaction code
# ─────────────────────────────────────────
def generate_tx_code():
    from datetime import datetime
    date_str = datetime.now().strftime('%Y%m%d')
    rand = ''.join(random.choices(string.digits, k=4))
    return f'TX-{date_str}-{rand}'


# ─────────────────────────────────────────
# HELPER: Send SMS via TextLink
# ─────────────────────────────────────────
def send_sms(phone_number, message):
    try:
        import requests
        from django.conf import settings

        # TextLink requires +63 country prefix format
        if phone_number.startswith('0'):
            phone_number = '+63' + phone_number[1:]
        elif phone_number.startswith('63'):
            phone_number = '+' + phone_number
        elif not phone_number.startswith('+'):
            phone_number = '+63' + phone_number

        print(f'[SMS] Sending to: {phone_number}')

        response = requests.post(
            'https://textlinksms.com/api/send-sms',
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {settings.TEXTLINK_API_KEY}',
            },
            json={
                'phone_number': phone_number,
                'text': message,
            }
        )

        print(f'[SMS] Status: {response.status_code}')
        print(f'[SMS] Response: {response.text}')

        result = response.json()
        if result.get('ok'):
            print(f'[SMS SENT] ✅ To: {phone_number}')
            return True
        else:
            print(f'[SMS ERROR] ❌ {result.get("message")}')
            return False

    except Exception as e:
        print(f'[SMS ERROR] ❌ {e}')
        return False

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def lookup_plate(request):
    plate = request.query_params.get('plate', '').upper().strip()
    plate_no_space = plate.replace(' ', '')  # ← remove spaces

    if not plate:
        return Response(
            {'error': 'Plate number required.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        # Try exact match first, then without spaces
        try:
            vehicle = VehicleRegistration.objects.get(plate_number=plate)
        except VehicleRegistration.DoesNotExist:
            vehicle = VehicleRegistration.objects.get(plate_number=plate_no_space)

        return Response({
            'found': True,
            'plate_number': vehicle.plate_number,
            'owner_name': vehicle.owner_name,
            'owner_phone': vehicle.owner_contact,
            'owner_address': '',
            'vehicle_make': vehicle.brand,
            'vehicle_model': vehicle.vehicle_type,
            'vehicle_color': vehicle.color,
            'vehicle_type': vehicle.vehicle_type,
        }, status=status.HTTP_200_OK)

    except VehicleRegistration.DoesNotExist:
        return Response({
            'found': False,
            'message': 'Plate not found in LTMS database.',
        }, status=status.HTTP_200_OK)
        
        
# ─────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────
@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    badge_id = request.data.get('badge_id')
    password = request.data.get('password')

    if not badge_id or not password:
        return Response(
            {'error': 'Please provide both badge ID and password.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        enforcer = Enforcer.objects.get(badge_id=badge_id)
    except Enforcer.DoesNotExist:
        return Response(
            {'error': 'Invalid badge ID or password.'},
            status=status.HTTP_401_UNAUTHORIZED
        )

    user = authenticate(username=enforcer.user.username, password=password)
    if not user:
        return Response(
            {'error': 'Invalid badge ID or password.'},
            status=status.HTTP_401_UNAUTHORIZED
        )

    token, created = Token.objects.get_or_create(user=user)

    return Response({
        'token': token.key,
        'enforcer': {
            'id': enforcer.id,
            'badge_id': enforcer.badge_id,
            'full_name': enforcer.full_name,
            'is_on_duty': enforcer.is_on_duty,
        }
    }, status=status.HTTP_200_OK)


# ─────────────────────────────────────────
# LOGOUT
# ─────────────────────────────────────────
@api_view(['POST'])
def logout_view(request):
    try:
        request.user.auth_token.delete()
    except Exception:
        pass
    return Response(
        {'message': 'Successfully logged out.'},
        status=status.HTTP_200_OK
    )
    
# ─────────────────────────────────────────
# ADMIN LOGIN (for the web dashboard)
# ─────────────────────────────────────────
@api_view(['POST'])
@permission_classes([AllowAny])
def admin_login(request):
    username = request.data.get('username')
    password = request.data.get('password')

    if not username or not password:
        return Response(
            {'error': 'Please provide username and password.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    user = authenticate(username=username, password=password)
    if not user or not user.is_staff:
        return Response(
            {'error': 'Invalid credentials or not an admin.'},
            status=status.HTTP_401_UNAUTHORIZED
        )

    token, _ = Token.objects.get_or_create(user=user)
    return Response({
        'token': token.key,
        'username': user.username,
        'is_superuser': user.is_superuser,
    }, status=status.HTTP_200_OK)
    
# ─────────────────────────────────────────
# RECEIVE DETECTION FROM YOLOv5+ALPR
# ─────────────────────────────────────────
@api_view(['POST'])
@permission_classes([AllowAny])  # ← YOLOv5 system calls this
def receive_detection(request):
    if request.headers.get('X-API-Key') != os.environ.get('DETECTION_API_KEY'):
        return Response({'error': 'Unauthorized.'}, status=status.HTTP_401_UNAUTHORIZED)
    
    plate_number = request.data.get('plate_number', '')
    location = request.data.get('location', '')
    confidence_score = request.data.get('confidence_score', 0.0)
    violation_type = request.data.get('violation_type', 'Illegal Parking')

    if not plate_number:
        return Response(
            {'error': 'Plate number required.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Save detection to database
    detection = Detection.objects.create(
        plate_number=plate_number.upper(),
        location=location,
        confidence_score=confidence_score,
        status='pending',
    )

    print(f'[DETECTION] New: {plate_number} at {location} ({confidence_score}%)')

    return Response({
        'id': detection.id,
        'plate_number': detection.plate_number,
        'location': detection.location,
        'confidence_score': str(detection.confidence_score),
        'status': detection.status,
        'detected_at':timezone.localtime(detection.detected_at).strftime('%Y-%m-%d %H:%M:%S'),
        'message': 'Detection received successfully.',
    }, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────
# GET PENDING DETECTIONS (Flutter polls this)
# ─────────────────────────────────────────
@api_view(['GET'])
def get_pending_detections(request):
    """
    Flutter app polls this endpoint to check for new detections.
    """
    detections = Detection.objects.filter(
        status='pending'
    ).order_by('-detected_at')

    data = []
    for d in detections:
        data.append({
            'id': d.id,
            'plate_number': d.plate_number,
            'location': d.location,
            'confidence_score': str(d.confidence_score),
            'status': d.status,
            'detected_at': timezone.localtime(d.detected_at).strftime('%b %d, %Y %I:%M %p'),
        })

    return Response(data, status=status.HTTP_200_OK)


# ─────────────────────────────────────────
# UPDATE DETECTION STATUS (confirm/decline)
# ─────────────────────────────────────────
@api_view(['PATCH'])
def update_detection(request, detection_id):
    try:
        detection = Detection.objects.get(id=detection_id)
    except Detection.DoesNotExist:
        return Response(
            {'error': 'Detection not found.'},
            status=status.HTTP_404_NOT_FOUND
        )

    new_status = request.data.get('status', '')
    if new_status in ['confirmed', 'declined']:
        detection.status = new_status
        detection.save()

    return Response({
        'id': detection.id,
        'status': detection.status,
        'message': f'Detection {new_status} successfully.',
    }, status=status.HTTP_200_OK)


# ─────────────────────────────────────────
# FINES (default amounts; editable in Django Admin > Violations)
# ─────────────────────────────────────────
FINE_SCHEDULE = {
    'Illegal Parking': 500,
    'No Parking Zone': 500,
    'Obstruction': 750,
    'Double Parking': 500,
    'No Loading/Unloading Zone': 500,
    'Blocking Driveway': 500,
    'Expired Registration': 1000,
    'No Valid OR/CR': 1000,
}


def resolve_violations(violation_text):
    """'A, B' -> (['A', 'B'], total fine)"""
    names = [n.strip() for n in violation_text.split(',') if n.strip()]
    total = 0
    for n in names:
        v, _ = Violation.objects.get_or_create(
            name=n,
            defaults={'fine_amount': FINE_SCHEDULE.get(n, 500)},
        )
        total += v.fine_amount
    return names, total


def build_sms_message(ticket, violation_names, total_fine):
    vehicle = f" ({ticket.vehicle_type})" if ticket.vehicle_type else ""
    issued = timezone.localtime(ticket.date_issued).strftime('%b %d, %Y %I:%M %p')
    return (
        f"[CITY OF BUTUAN - CTTMD]\n"
        f"TRAFFIC VIOLATION NOTICE\n\n"
        f"Plate No.: {ticket.plate_number}{vehicle}\n"
        f"Violation: {'; '.join(violation_names)}\n"
        f"Fine: PHP {total_fine:,.2f}\n"
        f"Location: {ticket.location}\n"
        f"Date/Time: {issued}\n"
        f"Ticket No.: {ticket.transaction_code}\n"
        f"Issued by: Officer {ticket.enforcer.badge_id}\n\n"
        f"Please settle this fine at the CTTMD Office, Butuan City within "
        f"five (5) working days, and present your Ticket No. or plate number. "
        f"Failure to settle may incur additional penalties."
    )

# ─────────────────────────────────────────
# SUBMIT TICKET (custom endpoint)
# ─────────────────────────────────────────
@api_view(['POST'])
def submit_ticket(request):
    try:
        token_key = request.META.get('HTTP_AUTHORIZATION', '').replace('Token ', '')
        token = Token.objects.get(key=token_key)
        enforcer = Enforcer.objects.get(user=token.user)
    except Exception:
        return Response(
            {'error': 'Unauthorized.'},
            status=status.HTTP_401_UNAUTHORIZED
        )

    # Get violations (can be several, separated by commas) and the total fine
    violation_name = request.data.get('violation_name', '')
    names, total_fine = resolve_violations(violation_name)
    if not names:
        return Response(
            {'error': 'At least one violation is required.'},
            status=status.HTTP_400_BAD_REQUEST
        )
    violation, _ = Violation.objects.update_or_create(
        name=', '.join(names),
        defaults={'fine_amount': total_fine},
    )

    # Create ticket
    ticket = Ticket.objects.create(
        transaction_code=generate_tx_code(),
        plate_number=request.data.get('plate_number', ''),
        vehicle_type=request.data.get('vehicle_type', ''),
        driver_name=str(request.data.get('driver_name', '')).strip(), 
        violation=violation,
        location=request.data.get('location', ''),
        enforcer=enforcer,
        status='unpaid',
    )

        # ── SMS NOTIFICATION ──
    phone_number = request.data.get('phone_number', '')
    sms_message = build_sms_message(ticket, names, total_fine)
    sms_sent = False

    if phone_number:
        sms_sent = send_sms(phone_number, sms_message)
    
    return Response({
        'id': ticket.id,
        'transaction_code': ticket.transaction_code,
        'plate_number': ticket.plate_number,
        'vehicle_type': ticket.vehicle_type,
        'driver_name': ticket.driver_name,
        'violation': ticket.violation.name,
        'location': ticket.location,
        'enforcer_badge': enforcer.badge_id,
        'enforcer_name': enforcer.full_name,
        'status': ticket.status,
        'date_issued': timezone.localtime(ticket.date_issued).strftime('%Y-%m-%d %H:%M:%S'),
        'fine_amount': str(total_fine),
        'sms_sent': sms_sent,
        'sms_message': sms_message,
    }, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────
# GET TICKETS (for history screen)
# ─────────────────────────────────────────
@api_view(['GET'])
def get_tickets(request):
    try:
        token_key = request.META.get('HTTP_AUTHORIZATION', '').replace('Token ', '')
        token = Token.objects.get(key=token_key)
        enforcer = Enforcer.objects.get(user=token.user)
    except Exception:
        return Response(
            {'error': 'Unauthorized.'},
            status=status.HTTP_401_UNAUTHORIZED
        )

    tickets = Ticket.objects.filter(enforcer=enforcer).order_by('-date_issued')

    data = []
    for t in tickets:
        data.append({
            'id': t.id,
            'transaction_code': t.transaction_code,
            'plate_number': t.plate_number,
            'vehicle_type': t.vehicle_type,
            'driver_name': t.driver_name,
            'violation': t.violation.name if t.violation else '',
            'location': t.location,
            'enforcer_badge': enforcer.badge_id,
            'enforcer_name': enforcer.full_name,
            'status': t.status,
            'date_issued':timezone.localtime(t.date_issued).strftime('%b %d, %Y %I:%M %p'),
        })

    return Response(data, status=status.HTTP_200_OK)

# ─────────────────────────────────────────
# ADMIN: helper to format a ticket
# ─────────────────────────────────────────
def ticket_to_dict(t, request=None):
    evidence = []
    for e in t.evidence.all():
        url = e.file.url
        if request:
            url = request.build_absolute_uri(url)
        evidence.append({'id': e.id, 'url': url, 'type': e.file_type})

    return {
        'id': t.id,
        'transaction_code': t.transaction_code,
        'plate_number': t.plate_number,
        'vehicle_type': t.vehicle_type,
        'driver_name': t.driver_name,
        'violation': t.violation.name if t.violation else '',
        'fine_amount': str(t.violation.fine_amount) if t.violation else '0',
        'location': t.location,
        'enforcer_badge': t.enforcer.badge_id if t.enforcer else '',
        'enforcer_name': t.enforcer.full_name if t.enforcer else '',
        'status': t.status,
        'date_issued': timezone.localtime(t.date_issued).strftime('%b %d, %Y %I:%M %p'),
        'date_paid': (
            timezone.localtime(t.date_paid).strftime('%b %d, %Y %I:%M %p')
            if t.date_paid else None
        ),
        'evidence': evidence,
    }

# ─────────────────────────────────────────
# ADMIN: search tickets by plate or TX code
# ─────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_search_tickets(request):
    q = request.query_params.get('q', '').strip().upper()
    status_filter = request.query_params.get('status', '').strip().lower()

    tickets = Ticket.objects.select_related('violation', 'enforcer').prefetch_related('evidence').annotate(
    plate_clean=Replace('plate_number', Value(' '), Value(''))
).order_by('-date_issued')

    if q:
        tickets = tickets.filter(
            Q(plate_clean__icontains=q.replace(' ', '')) |
            Q(transaction_code__icontains=q)
        )
    if status_filter in ('paid', 'unpaid'):
        tickets = tickets.filter(status=status_filter)

    return Response([ticket_to_dict(t) for t in tickets[:100]])


# ─────────────────────────────────────────
# ADMIN: mark a ticket as paid
# ─────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAdminUser])
def admin_mark_paid(request, ticket_id):
    try:
        ticket = Ticket.objects.select_related('violation', 'enforcer').get(id=ticket_id)
    except Ticket.DoesNotExist:
        return Response({'error': 'Ticket not found.'}, status=status.HTTP_404_NOT_FOUND)

    if ticket.status == 'paid':
        return Response(
            {'error': 'Ticket is already paid.', 'ticket': ticket_to_dict(ticket)},
            status=status.HTTP_400_BAD_REQUEST
        )

    ticket.status = 'paid'
    ticket.date_paid = timezone.now()
    ticket.save()

    return Response(ticket_to_dict(ticket), status=status.HTTP_200_OK)

# ─────────────────────────────────────────
# ADMIN: create an enforcer account
# ─────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAdminUser])
def admin_create_enforcer(request):
    badge_id = str(request.data.get('badge_id', '')).strip()
    full_name = str(request.data.get('full_name', '')).strip()
    password = str(request.data.get('password', ''))
    contact_number = str(request.data.get('contact_number', '')).strip()

    if not badge_id or not full_name or not password:
        return Response(
            {'error': 'Badge ID, full name, and password are required.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    if len(password) < 6:
        return Response(
            {'error': 'Password must be at least 6 characters.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    if (Enforcer.objects.filter(badge_id=badge_id).exists()
            or User.objects.filter(username=badge_id).exists()):
        return Response(
            {'error': 'Badge ID already exists.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    with transaction.atomic():
        user = User.objects.create_user(username=badge_id, password=password)
        enforcer = Enforcer.objects.create(
            user=user,
            badge_id=badge_id,
            full_name=full_name,
            contact_number=contact_number,
            is_on_duty=True,
        )

    return Response({
        'id': enforcer.id,
        'badge_id': enforcer.badge_id,
        'full_name': enforcer.full_name,
        'contact_number': enforcer.contact_number,
        'is_on_duty': enforcer.is_on_duty,
    }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_evidence(request, ticket_id):
    files = request.FILES.getlist('files')

    try:
        ticket = Ticket.objects.get(id=ticket_id)
    except Ticket.DoesNotExist:
        return Response(
            {'error': 'Ticket not found.'},
            status=status.HTTP_404_NOT_FOUND
        )

    video_ext = ('.mp4', '.mov', '.avi', '.mkv', '.3gp', '.webm')
    for f in files:
        is_video = (
            (f.content_type or '').startswith('video')
            or f.name.lower().endswith(video_ext)
        )
        Evidence.objects.create(
            ticket=ticket,
            file=f,
            file_type='video' if is_video else 'photo',
        )

    return Response({'success': True, 'message': f'{len(files)} file(s) uploaded'})

# ─────────────────────────────────────────
# VIEWSETS (CRUD)
# ─────────────────────────────────────────
class EnforcerViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminUser]
    queryset = Enforcer.objects.all()
    serializer_class = EnforcerSerializer

    def perform_update(self, serializer):
        enforcer = serializer.save()
        password = self.request.data.get('password')
        if password:
            enforcer.user.set_password(str(password))
            enforcer.user.save()

    def perform_destroy(self, instance):
        user = instance.user
        instance.delete()
        user.delete()


class ViolationViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminUser]
    queryset = Violation.objects.all()
    serializer_class = ViolationSerializer


class TicketViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminUser]
    queryset = Ticket.objects.all().order_by('-date_issued')
    serializer_class = TicketSerializer


class DetectionViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminUser]
    queryset = Detection.objects.all().order_by('-detected_at')
    serializer_class = DetectionSerializer