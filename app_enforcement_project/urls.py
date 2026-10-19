from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.urls import re_path
from django.views.static import serve
from django.conf import settings

urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('app_enforcement_api.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)