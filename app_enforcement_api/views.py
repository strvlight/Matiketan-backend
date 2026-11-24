from rest_framework import viewsets, status
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.authtoken.models import Token
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from django.utils import timezone
from django.db.models import Q, Value
from django.db.models.functions import Replace
from django.db import transaction, IntegrityError, connection
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.conf import settings
from .push import notify_detection
import os
import re
import zlib
import base64
import random, string
from io import BytesIO
from PIL import Image

from .models import (
    Enforcer, Evidence, Violation, Ticket, Detection, DetectionImage,
    DeclineEvidence, VehicleRegistration, DeviceToken, TicketSignature,
    EnforcerSignature,
)
from .signature_utils import decode_signature, validate_png, MAX_SIGNATURE_BYTES
from .serializer import (
    EnforcerSerializer, ViolationSerializer,
    TicketSerializer, DetectionSerializer
)


# ─────────────────────────────────────────
# HELPER: Generate transaction code
# ─────────────────────────────────────────
def generate_tx_code():
    while True:
        code = 'TX-' + ''.join(random.choices(string.digits, k=5))
        if not Ticket.objects.filter(transaction_code=code).exists():
            return code


# ─────────────────────────────────────────
# HELPER: Send SMS via TextLink
# ─────────────────────────────────────────
def send_sms(phone_number, message):
    try:
        import requests
        from django.conf import settings

        # Remove spaces, dashes and other characters (keeps digits and +)
        phone_number = re.sub(r'[^\d+]', '', str(phone_number or ''))

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


# ─────────────────────────────────────────
# HELPER: Query the separate LTMS Supabase project
# ─────────────────────────────────────────
def ltms_fetch_vehicle(plate):
    """Returns (status, row). status: 'found' | 'not_found' | 'error'"""
    import requests

    if not settings.LTMS_SUPABASE_URL or not settings.LTMS_SERVICE_ROLE_KEY:
        return 'error', None

    try:
        res = requests.get(
    f'{settings.LTMS_SUPABASE_URL}/rest/v1/vehicles',
    headers={
        'apikey': settings.LTMS_SERVICE_ROLE_KEY,
        'Authorization': f'Bearer {settings.LTMS_SERVICE_ROLE_KEY}',
    },
    params={
        'plate_number': f'ilike.{plate}',
        'select': 'plate_number,vehicle_type,brand,model,color,owners(full_name,mobile_number)',
    },
    timeout=5,
)
        res.raise_for_status()
        rows = res.json()
    except Exception as e:
        print(f'[LTMS] Lookup failed: {e}')
        return 'error', None

    if not rows:
        return 'not_found', None
    return 'found', rows[0]


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def lookup_plate(request):
    plate = re.sub(r'[^A-Za-z0-9]', '', request.query_params.get('plate', '')).upper()

    if not plate:
        return Response(
            {'error': 'Plate number required.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    result, vehicle = ltms_fetch_vehicle(plate)

    if result == 'found':
        owner = vehicle.get('owners') or {}
        return Response({
            'found': True,
            'plate_number': vehicle['plate_number'],
            'owner_name': owner.get('full_name') or '',
            'owner_phone': owner.get('mobile_number') or '',
            'owner_address': '',
            'vehicle_make': vehicle['brand'],
            'vehicle_model': vehicle['model'],
            'vehicle_color': vehicle['color'],
            'vehicle_type': vehicle['vehicle_type'],
        }, status=status.HTTP_200_OK)

    if result == 'error':
        return Response({
            'found': False,
            'message': 'LTMS database is unavailable. Enter the details manually.',
        }, status=status.HTTP_200_OK)

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
            'contact_number': enforcer.contact_number,
            'is_on_duty': enforcer.is_on_duty,
            'assigned_area': enforcer.assigned_area,
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
def compress_image(uploaded_file, max_width=800, quality=70):
    img = Image.open(uploaded_file).convert('RGB')
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)))
    buf = BytesIO()
    img.save(buf, format='JPEG', quality=quality)
    return buf.getvalue()



def process_detection(plate_number, location, confidence_score, image_file):
    now = timezone.now()
    window = timedelta(minutes=settings.DUPLICATE_WINDOW_MINUTES)
    limit = settings.PARKING_LIMIT_SECONDS

    image_bytes = None
    if image_file:
        try:
            image_bytes = compress_image(image_file)
        except Exception as e:
            print(f'[DETECTION] Image skipped: {e}')

    to_notify = None
    response = None

    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(%s)',
                        [zlib.crc32(plate_number.encode())])

        existing = Detection.objects.select_for_update().filter(
            plate_number=plate_number,
            status__in=['monitoring', 'pending', 'in_progress'],
            last_seen__gte=now - window,
        ).order_by('-last_seen').first()

        if existing:
            existing.last_seen = now
            existing.sighting_count += 1
            if location:
                existing.location = location
            parked_for = (now - existing.detected_at).total_seconds()
            became_pending = (existing.status == 'monitoring'
                              and parked_for >= limit)
            if became_pending:
                existing.status = 'pending'
                to_notify = existing
            existing.save()
            response = Response({
                'id': existing.id,
                'plate_number': existing.plate_number,
                'status': existing.status,
                'duplicate': True,
                'parked_seconds': int(parked_for),
                'sighting_count': existing.sighting_count,
                'message': 'Existing detection updated.',
            }, status=status.HTTP_200_OK)
        else:
            recently_done = Detection.objects.filter(
                plate_number=plate_number
            ).filter(
                Q(status='confirmed',
                  last_seen__gte=now - timedelta(hours=settings.COOLDOWN_CONFIRMED_HOURS)) |
                Q(status='declined',
                  declined_at__gte=now - timedelta(hours=settings.COOLDOWN_DECLINED_HOURS))
            ).exists()

            if recently_done:
                response = Response(
                    {'skipped': True, 'reason': 'cooldown',
                     'plate_number': plate_number},
                    status=status.HTTP_200_OK)
            else:
                detection = Detection.objects.create(
                    plate_number=plate_number,
                    location=location,
                    confidence_score=confidence_score,
                    status='pending' if limit <= 0 else 'monitoring',
                    last_seen=now,
                )
                has_image = False
                if image_bytes:
                    DetectionImage.objects.create(
                        detection=detection, data=image_bytes)
                    has_image = True
                if detection.status == 'pending':
                    to_notify = detection
                response = Response({
                    'id': detection.id,
                    'plate_number': detection.plate_number,
                    'status': detection.status,
                    'has_image': has_image,
                    'duplicate': False,
                    'message': 'Detection received successfully.',
                }, status=status.HTTP_201_CREATED)

    if to_notify:
        notify_detection(to_notify)
    return response


@api_view(['POST'])
@permission_classes([AllowAny])
def receive_detection(request):
    key = request.META.get('HTTP_X_DETECTOR_KEY', '')
    if not settings.DETECTOR_API_KEY or key != settings.DETECTOR_API_KEY:
        return Response({'error': 'Invalid detector key.'},
                        status=status.HTTP_401_UNAUTHORIZED)

    plate_number = str(request.data.get('plate_number', '')).strip().upper()
    if not plate_number:
        return Response({'error': 'Plate number required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    return process_detection(
        plate_number,
        request.data.get('location', ''),
        request.data.get('confidence_score', 0.0),
        request.FILES.get('image'),
    )
  
@api_view(['POST'])
@permission_classes([AllowAny])  # protected by the detector key
@parser_classes([MultiPartParser, FormParser])
def detect_from_image(request):
    key = request.META.get('HTTP_X_DETECTOR_KEY', '') or request.data.get('key', '')
    if not settings.DETECTOR_API_KEY or key != settings.DETECTOR_API_KEY:
        return Response({'error': 'Invalid detector key.'},
                        status=status.HTTP_401_UNAUTHORIZED)

    image = request.FILES.get('image')
    if not image:
        return Response({'error': 'Image required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    import requests
    try:
        r = requests.post(
            'https://api.platerecognizer.com/v1/plate-reader/',
            files={'upload': (image.name, image.read())},
            headers={'Authorization': f'Token {settings.PLATE_RECOGNIZER_TOKEN}'},
            timeout=30,
        )
        results = r.json().get('results', [])
    except Exception as e:
        return Response({'error': f'Plate recognition failed: {e}'},
                        status=status.HTTP_502_BAD_GATEWAY)

    if not results:
        return Response({'detected': False, 'message': 'No plate found in the photo.'})

    best = results[0]
    image.seek(0)
    return process_detection(
        best['plate'].upper(),
        request.data.get('location', 'Montilla Blvd., Butuan City'),
        round(best['score'] * 100, 2),
        image,
    )
# ─────────────────────────────────────────
# GET PENDING DETECTIONS (Flutter polls this)
# ─────────────────────────────────────────
def release_expired_claims():
    """A claim that nobody finished goes back to 'pending' after CLAIM_MINUTES."""
    minutes = getattr(settings, 'CLAIM_MINUTES', 10)
    Detection.objects.filter(
        status='in_progress',
        claimed_at__lt=timezone.now() - timedelta(minutes=minutes),
    ).update(status='pending', claimed_by=None, claimed_at=None)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_pending_detections(request):
    """
    Flutter app polls this endpoint to check for new detections.
    Shows 'pending' ones, plus the ones this officer has claimed.
    Detections claimed by another officer are hidden.
    """
    release_expired_claims()
    me = Enforcer.objects.filter(user=request.user).first()
    detections = Detection.objects.filter(
        Q(status='pending') | Q(status='in_progress', claimed_by=me)
    ).order_by('-detected_at')

    data = []
    for d in detections:
        data.append({
            'id': d.id,
            'plate_number': d.plate_number,
            'location': d.location,
            'confidence_score': str(d.confidence_score),
            'status': d.status,
            'claimed_by_me': d.status == 'in_progress',
            'has_image': DetectionImage.objects.filter(detection=d).exists(),
            'detected_at': timezone.localtime(d.detected_at).strftime('%b %d, %Y %I:%M %p'),
        })

    return Response(data, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_detection_image(request, detection_id):
    try:
        img = DetectionImage.objects.get(detection_id=detection_id)
    except DetectionImage.DoesNotExist:
        return Response({'error': 'No image.'}, status=status.HTTP_404_NOT_FOUND)
    return HttpResponse(bytes(img.data), content_type=img.content_type)

# ─────────────────────────────────────────
# UPDATE DETECTION STATUS (confirm/decline)
# ─────────────────────────────────────────
@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def update_detection(request, detection_id):
    new_status = request.data.get('status', '')
    # the Flutter app may send 'claim' / 'release'
    new_status = {'claim': 'in_progress', 'release': 'pending'}.get(new_status, new_status)

    with transaction.atomic():
        try:
            # lock the row so two officers can't act on it at the same time
            detection = Detection.objects.select_for_update().get(id=detection_id)
        except Detection.DoesNotExist:
            return Response({'error': 'Detection not found.'},
                            status=status.HTTP_404_NOT_FOUND)

        me = Enforcer.objects.filter(user=request.user).first()

        # an expired claim counts as free again
        minutes = getattr(settings, 'CLAIM_MINUTES', 10)
        if (detection.status == 'in_progress' and detection.claimed_at
                and detection.claimed_at < timezone.now() - timedelta(minutes=minutes)):
            detection.status = 'pending'
            detection.claimed_by = None
            detection.claimed_at = None
            detection.save()

        # ── CLAIM: officer opens the alert ──
        if new_status == 'in_progress':
            if detection.status == 'in_progress' and detection.claimed_by_id == (me.id if me else None):
                detection.claimed_at = timezone.now()   # same officer: refresh the timer
                detection.save()
            elif detection.status == 'pending':
                detection.status = 'in_progress'
                detection.claimed_by = me
                detection.claimed_at = timezone.now()
                detection.save()
            else:
                who = detection.claimed_by.full_name if detection.claimed_by else 'another officer'
                msg = (f'Being handled by {who}.' if detection.status == 'in_progress'
                       else f'Already {detection.status}.')
                return Response({'error': msg, 'status': detection.status},
                                status=status.HTTP_409_CONFLICT)
            return Response({'id': detection.id, 'status': detection.status,
                             'message': 'Detection claimed.'}, status=status.HTTP_200_OK)

        # ── RELEASE: officer backs out ──
        if new_status == 'pending':
            if detection.status == 'in_progress' and detection.claimed_by_id == (me.id if me else None):
                detection.status = 'pending'
                detection.claimed_by = None
                detection.claimed_at = None
                detection.save()
            return Response({'id': detection.id, 'status': detection.status,
                             'message': 'Detection released.'}, status=status.HTTP_200_OK)

        if new_status in ['confirmed', 'declined']:
            # same officer re-confirming (e.g. submit_ticket already confirmed it): ok
            if (new_status == 'confirmed' and detection.status == 'confirmed'
                    and me and detection.reviewed_by_id == me.id):
                return Response({'id': detection.id, 'status': detection.status,
                                 'message': 'Detection confirmed successfully.'},
                                status=status.HTTP_200_OK)

            # only a pending one, or one claimed by THIS officer, can be confirmed/declined
            allowed = (detection.status == 'pending' or
                       (detection.status == 'in_progress' and me
                        and detection.claimed_by_id == me.id))
            if not allowed:
                if detection.status == 'in_progress':
                    who = detection.claimed_by.full_name if detection.claimed_by else 'another officer'
                    msg = f'Being handled by {who}.'
                else:
                    who = detection.reviewed_by.full_name if detection.reviewed_by else 'another officer'
                    msg = f'Already {detection.status} by {who}.'
                return Response({'error': msg, 'status': detection.status},
                                status=status.HTTP_409_CONFLICT)

            detection.status = new_status
            detection.reviewed_by = me
            detection.claimed_by = None
            detection.claimed_at = None

            if new_status == 'declined':
                detection.decline_reason = str(request.data.get('reason', '')).strip()
                detection.decline_notes = str(request.data.get('notes', '')).strip()
                detection.declined_at = timezone.now()

            detection.save()

            if new_status == 'declined':
                for f in request.FILES.getlist('evidence'):
                    try:
                        DeclineEvidence.objects.create(
                            detection=detection, data=compress_image(f)
                        )
                    except Exception as e:
                        print(f'[DECLINE] Evidence skipped: {e}')

    return Response({
        'id': detection.id,
        'status': detection.status,
        'message': f'Detection {new_status} successfully.',
    }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def register_device(request):
    token = str(request.data.get('token', '')).strip()
    if not token:
        return Response({'error': 'Token required.'},
                        status=status.HTTP_400_BAD_REQUEST)
    DeviceToken.objects.update_or_create(
        token=token, defaults={'user': request.user}
    )
    return Response({'ok': True}, status=status.HTTP_200_OK)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_profile(request):
    e = Enforcer.objects.filter(user=request.user).first()
    if not e:
        return Response({'error': 'No enforcer.'}, status=status.HTTP_404_NOT_FOUND)
    return Response({
        'id': e.id,
        'full_name': e.full_name,
        'badge_id': e.badge_id,
        'contact_number': e.contact_number,
        'assigned_area': e.assigned_area,
        'has_photo': bool(e.photo_data),
    })


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def profile_photo(request):
    e = Enforcer.objects.filter(user=request.user).first()
    if not e:
        return Response({'error': 'No enforcer.'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'POST':
        f = request.FILES.get('photo')
        if not f:
            return Response({'error': 'Photo required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            e.photo_data = compress_image(f, max_width=400, quality=80)
            e.photo_type = 'image/jpeg'
            e.save()
        except Exception as ex:
            return Response({'error': f'Invalid image: {ex}'},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({'ok': True})

    if not e.photo_data:
        return Response({'error': 'No photo.'}, status=status.HTTP_404_NOT_FOUND)
    return HttpResponse(bytes(e.photo_data), content_type=e.photo_type)

# ─────────────────────────────────────────
# DECLINED HISTORY (this enforcer's declines)
# ─────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_declined_detections(request):
    enforcer = Enforcer.objects.filter(user=request.user).first()
    qs = Detection.objects.filter(status='declined').select_related('reviewed_by')
    if enforcer and not request.user.is_staff:
        qs = qs.filter(reviewed_by=enforcer)
    qs = qs.order_by('-declined_at', '-detected_at')[:100]

    data = []
    for d in qs:
        when = d.declined_at or d.detected_at
        data.append({
            'id': d.id,
            'plate_number': d.plate_number,
            'location': d.location,
            'confidence_score': str(d.confidence_score),
            'decline_reason': d.decline_reason,
            'decline_notes': d.decline_notes,
            'has_evidence': d.decline_evidence.exists(),
            'declined_at': timezone.localtime(when).strftime('%b %d, %Y %I:%M %p'),
            'declined_by': d.reviewed_by.full_name if d.reviewed_by else None,
            'declined_by_badge': d.reviewed_by.badge_id if d.reviewed_by else None,
        })
    return Response(data, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_decline_evidence(request, detection_id):
    ev = DeclineEvidence.objects.filter(detection_id=detection_id).first()
    if not ev:
        return Response({'error': 'No evidence.'}, status=status.HTTP_404_NOT_FOUND)
    return HttpResponse(bytes(ev.data), content_type=ev.content_type)


def _data_url(data, ctype):
    return f"data:{ctype or 'image/jpeg'};base64," + base64.b64encode(bytes(data)).decode()


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_declined_detail(request, detection_id):
    try:
        d = Detection.objects.get(id=detection_id)
    except Detection.DoesNotExist:
        return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

    images = []
    di = DetectionImage.objects.filter(detection=d).first()
    if di:
        images.append(_data_url(di.data, di.content_type))
    for ev in d.decline_evidence.all().order_by('id'):
        images.append(_data_url(ev.data, ev.content_type))

    return Response({'id': d.id, 'images': images})

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
    
def ticket_sms_text(t):
    """Rebuild the SMS text of an existing ticket."""
    names = [
        n.strip()
        for n in (t.violation.name if t.violation else '').split(',')
        if n.strip()
    ]
    fine = t.violation.fine_amount if t.violation else 0
    return build_sms_message(t, names, fine) 

@api_view(['POST', 'GET'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def ticket_signature(request, ticket_id):
    try:
        ticket = Ticket.objects.get(id=ticket_id)
    except Ticket.DoesNotExist:
        return Response({'error': 'Ticket not found.'},
                        status=status.HTTP_404_NOT_FOUND)

    if request.method == 'POST':
        f = request.FILES.get('signature')
        if not f:
            return Response({'error': 'Signature required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        ticket.signature_data = f.read()
        ticket.save()
        return Response({'ok': True})

    if not ticket.signature_data:
        return Response({'error': 'No signature.'},
                        status=status.HTTP_404_NOT_FOUND)
    return HttpResponse(bytes(ticket.signature_data), content_type='image/png')   

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

    # Enforcer signature (optional). If one is sent but invalid, the ticket is
    # rejected before anything is created.
    enforcer_signature_png = None
    raw_enforcer_signature = request.data.get('enforcer_signature')
    if raw_enforcer_signature:
        try:
            enforcer_signature_png = decode_signature(raw_enforcer_signature, 'Enforcer signature')
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

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

    # Create ticket (the enforcer signature, if sent, is saved in the same transaction)
    detection_id = request.data.get('detection_id')
    with transaction.atomic():
        # If the ticket comes from a detection, lock it and make sure this officer
        # still owns it. A second officer is rejected BEFORE any ticket is created.
        det = None
        if detection_id:
            try:
                det = Detection.objects.select_for_update().get(id=detection_id)
            except (Detection.DoesNotExist, ValueError):
                return Response({'error': 'Detection not found.'},
                                status=status.HTTP_404_NOT_FOUND)
            mine = det.status == 'in_progress' and det.claimed_by_id == enforcer.id
            if not (mine or det.status == 'pending'):
                who = (det.claimed_by or det.reviewed_by)
                who = who.full_name if who else 'another officer'
                return Response(
                    {'error': f'This detection is already {det.status} by {who}.',
                     'status': det.status},
                    status=status.HTTP_409_CONFLICT)

        ticket = Ticket.objects.create(
            transaction_code=generate_tx_code(),
            plate_number=request.data.get('plate_number', ''),
            vehicle_type=request.data.get('vehicle_type', ''),
            vehicle_color=request.data.get('vehicle_color', ''),
            driver_name=str(request.data.get('driver_name', '')).strip(), 
            violation=violation,
            location=request.data.get('location', ''),
            enforcer=enforcer,
            status='unpaid',
        )
        if enforcer_signature_png:
            EnforcerSignature.objects.create(ticket=ticket, data=enforcer_signature_png)
        if det:
            det.status = 'confirmed'
            det.reviewed_by = enforcer
            det.claimed_by = None
            det.claimed_at = None
            det.save()

        # ── SMS NOTIFICATION ──
    phone_number = request.data.get('phone_number', '')
    print(f'[TICKET] phone_number received: "{phone_number}"')
    sms_message = build_sms_message(ticket, names, total_fine)
    sms_sent = False

    if phone_number:
        sms_sent = send_sms(phone_number, sms_message)
    
    return Response({
        'id': ticket.id,
        'transaction_code': ticket.transaction_code,
        'plate_number': ticket.plate_number,
        'vehicle_color': ticket.vehicle_color,
        'vehicle_type': ticket.vehicle_type,
        'driver_name': ticket.driver_name,
        'violation': ticket.violation.name,
        'location': ticket.location,
        'enforcer_badge': enforcer.badge_id,
        'enforcer_name': enforcer.full_name,
        'status': ticket.status,
        'date_issued': timezone.localtime(ticket.date_issued).strftime('%b %d, %Y %I:%M %p'),
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

    tickets = (Ticket.objects.filter(enforcer=enforcer)
           .select_related('violation')
           .prefetch_related('evidence')
           .order_by('-date_issued'))
    
    sigs = {
        s.ticket_id: s.data
        for s in EnforcerSignature.objects.filter(ticket__in=tickets)
    }

    data = []
    for t in tickets:
        sig_bytes = sigs.get(t.id)
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
            'fine_amount': str(t.violation.fine_amount) if t.violation else '0', 
            'sms_message': ticket_sms_text(t),
            'date_issued':timezone.localtime(t.date_issued).strftime('%b %d, %Y %I:%M %p'),
            'vehicle_color': t.vehicle_color,
            'evidence': [
                {'url': request.build_absolute_uri(e.file.url), 'type': e.file_type}
                for e in t.evidence.all()
            ],
            'signature_b64': (
                base64.b64encode(bytes(sig_bytes)).decode()
                if sig_bytes else ''
            ),
            'date_paid': (
                timezone.localtime(t.date_paid).strftime('%b %d, %Y %I:%M %p')
                if t.date_paid else ''
            ),  
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

    has_signature = (
        TicketSignature.objects.filter(ticket_id=t.id).exists()
        if t.status == 'paid' else False
    )
    has_enforcer_signature = EnforcerSignature.objects.filter(ticket_id=t.id).exists()

    signature_url = None
    if has_signature:
        signature_url = f'/api/admin/tickets/{t.id}/signature/'
        if request:
            signature_url = request.build_absolute_uri(signature_url)

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
        # Exact timestamp used by the dashboard to sort History by settlement time
        'date_paid_iso': t.date_paid.isoformat() if t.date_paid else None,
        'receipt_number': t.receipt_number,
        'owner_name': t.owner_name,
        'amount_paid': str(t.amount_paid) if t.amount_paid is not None else None,
        'payment_method': t.payment_method,
        'remarks': t.remarks,
        # Administrator who processed the settlement (None for unpaid/older tickets)
        'processed_by': (
            (t.processed_by.get_full_name() or t.processed_by.username)
            if t.processed_by else None
        ),
        'has_signature': has_signature,
        'signature_url': signature_url,
        'has_enforcer_signature': has_enforcer_signature,
        'evidence': evidence,
        'vehicle_color': t.vehicle_color,
    }

# ─────────────────────────────────────────
# ADMIN: search tickets by plate or TX code
# ─────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_search_tickets(request):
    q = request.query_params.get('q', '').strip().upper()
    status_filter = request.query_params.get('status', '').strip().lower()

    tickets = Ticket.objects.select_related('violation', 'enforcer', 'processed_by').prefetch_related('evidence').annotate(
    plate_clean=Replace('plate_number', Value(' '), Value(''))
).order_by('-date_issued')

    if q:
        tickets = tickets.filter(
            Q(plate_clean__icontains=q.replace(' ', '')) |
            Q(transaction_code__icontains=q)
        )
    if status_filter in ('paid', 'unpaid'):
        tickets = tickets.filter(status=status_filter)

    return Response([ticket_to_dict(t, request) for t in tickets[:100]])


# Receipt numbers are copied from the official receipt: letters, digits, - and /
RECEIPT_PATTERN = re.compile(r'^[A-Z0-9][A-Z0-9\-/]{2,29}$')


# ─────────────────────────────────────────
# ADMIN: mark a ticket as paid (with manual payment details + violator signature)
# ─────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAdminUser])
def admin_mark_paid(request, ticket_id):
    # Normalize so 'or-001 ' and 'OR-001' count as the same receipt number
    receipt_number = re.sub(r'\s+', '', str(request.data.get('receipt_number') or '')).upper()
    owner_name = str(request.data.get('owner_name') or '').strip()
    payment_method = str(request.data.get('payment_method') or '').strip() or 'Cash'
    remarks = str(request.data.get('remarks') or '').strip()

    if not receipt_number:
        return Response({'error': 'Receipt number is required.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if not RECEIPT_PATTERN.match(receipt_number):
        return Response(
            {'error': 'Receipt number must be 3-30 characters: letters, numbers, - or / only.'},
            status=status.HTTP_400_BAD_REQUEST)
    if not owner_name:
        return Response({'error': "Vehicle owner's name is required."},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        amount_paid = Decimal(str(request.data.get('amount_paid')))
        if amount_paid <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError, TypeError):
        return Response({'error': 'Enter a valid amount paid.'},
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        ticket = Ticket.objects.select_related('violation', 'enforcer', 'processed_by').get(id=ticket_id)
    except Ticket.DoesNotExist:
        return Response({'error': 'Ticket not found.'}, status=status.HTTP_404_NOT_FOUND)

    if ticket.status == 'paid':
        return Response(
            {'error': 'Ticket is already paid.', 'ticket': ticket_to_dict(ticket, request)},
            status=status.HTTP_400_BAD_REQUEST
        )

    if Ticket.objects.filter(receipt_number__iexact=receipt_number).exclude(id=ticket.id).exists():
        return Response({'error': 'That receipt number is already used on another ticket.'},
                        status=status.HTTP_400_BAD_REQUEST)

    # The violator's signature is required; a missing or fake one stops the
    # settlement before any ticket field changes
    try:
        signature_png = decode_signature(request.data.get('violator_signature'))
    except ValueError as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    ticket.status = 'paid'
    ticket.date_paid = timezone.now()
    ticket.receipt_number = receipt_number
    ticket.owner_name = owner_name
    ticket.amount_paid = amount_paid
    ticket.payment_method = payment_method
    ticket.remarks = remarks or None
    # Record which administrator processed this settlement (taken from the
    # logged-in user on the server, never from the request body)
    ticket.processed_by = request.user

    try:
        # Ticket and signature are saved together or not at all
        with transaction.atomic():
            ticket.save()
            TicketSignature.objects.create(ticket=ticket, data=signature_png)
    except IntegrityError:
        return Response({'error': 'That receipt number is already used on another ticket.'},
                        status=status.HTTP_400_BAD_REQUEST)

    return Response(ticket_to_dict(ticket, request), status=status.HTTP_200_OK)


# ─────────────────────────────────────────
# ADMIN: serve a ticket's violator signature (token required)
# ─────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_ticket_signature(request, ticket_id):
    sig = TicketSignature.objects.filter(ticket_id=ticket_id).first()
    if not sig:
        return Response({'error': 'No signature.'}, status=status.HTTP_404_NOT_FOUND)
    return HttpResponse(bytes(sig.data), content_type=sig.content_type)


# ─────────────────────────────────────────
# ADMIN: serve a ticket's enforcer signature (token required)
# ─────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_ticket_enforcer_signature(request, ticket_id):
    sig = EnforcerSignature.objects.filter(ticket_id=ticket_id).first()
    if not sig:
        return Response({'error': 'No signature.'}, status=status.HTTP_404_NOT_FOUND)
    return HttpResponse(bytes(sig.data), content_type=sig.content_type)

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
    assigned_area = str(request.data.get('assigned_area', '')).strip()

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
            assigned_area=assigned_area,
            is_on_duty=True,
            # Administrator who created this enforcer (taken from the
            # logged-in user on the server, never from the request body)
            created_by=request.user,
        )

    return Response({
        'id': enforcer.id,
        'badge_id': enforcer.badge_id,
        'full_name': enforcer.full_name,
        'contact_number': enforcer.contact_number,
        'assigned_area': enforcer.assigned_area,
        'is_on_duty': enforcer.is_on_duty,
    }, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────
# ADMIN: upload an enforcer's profile photo (saved in the DB, same as mobile)
# ─────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAdminUser])
@parser_classes([MultiPartParser, FormParser])
def admin_enforcer_photo(request, enforcer_id):
    try:
        e = Enforcer.objects.get(id=enforcer_id)
    except Enforcer.DoesNotExist:
        return Response({'error': 'Enforcer not found.'}, status=status.HTTP_404_NOT_FOUND)

    f = request.FILES.get('photo')
    if not f:
        return Response({'error': 'Photo required.'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        e.photo_data = compress_image(f, max_width=400, quality=80)
        e.photo_type = 'image/jpeg'
        e.save()
    except Exception as ex:
        return Response({'error': f'Invalid image: {ex}'}, status=status.HTTP_400_BAD_REQUEST)

    return Response(EnforcerSerializer(e, context={'request': request}).data)


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
# MOBILE: attach the enforcer's signature to a ticket (multipart, field 'signature')
# Sent by the app right after the ticket is created. Write-once.
# ─────────────────────────────────────────
@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def upload_enforcer_signature(request, ticket_id):
    try:
        ticket = Ticket.objects.select_related('enforcer__user').get(id=ticket_id)
    except Ticket.DoesNotExist:
        return Response({'error': 'Ticket not found.'}, status=status.HTTP_404_NOT_FOUND)

    # Only the enforcer who issued the ticket (or an admin) may attach the signature
    issuer = ticket.enforcer.user if ticket.enforcer else None
    if not (request.user.is_staff or (issuer is not None and issuer == request.user)):
        return Response({'error': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)

    f = request.FILES.get('signature')
    if not f:
        return Response({'error': 'Signature required.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        raw = validate_png(f.read(MAX_SIGNATURE_BYTES + 1), 'Enforcer signature')
    except ValueError as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    # A saved signature is never replaced
    if EnforcerSignature.objects.filter(ticket_id=ticket.id).exists():
        return Response({'error': 'Signature already saved.'}, status=status.HTTP_409_CONFLICT)

    try:
        EnforcerSignature.objects.create(ticket=ticket, data=raw)
    except IntegrityError:
        return Response({'error': 'Signature already saved.'}, status=status.HTTP_409_CONFLICT)

    return Response({'success': True}, status=status.HTTP_201_CREATED)

# ─────────────────────────────────────────
# VIEWSETS (CRUD)
# ─────────────────────────────────────────
class EnforcerViewSet(viewsets.ModelViewSet):
    parser_classes = [MultiPartParser, FormParser, JSONParser]
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