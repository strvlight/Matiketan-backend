from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.urls import re_path
from django.views.static import serve
from django.conf import settings

from app_enforcement_api.views import get_detection_image

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('app_enforcement_api.urls')),
       path('detections/<int:detection_id>/image/', get_detection_image, name='detection_image'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]