from django.conf.urls import url, include
from rest_framework import routers
from . import views

router = routers.DefaultRouter()
router.register(r'users', views.UserViewSet)
router.register(r'groups', views.GroupViewSet)
router.register(r'sources', views.SourceViewSet)
router.register(r'registrations', views.RegistrationViewSet)


# Wire up our API using automatic URL routing.
urlpatterns = [
    url(r'^api/v2/', include(router.urls)),
]
