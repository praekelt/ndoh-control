import json
import responses
from django.contrib.auth.models import User
from django.test import TestCase
from django.db.models.signals import post_save
from rest_framework.test import APIClient
from rest_framework.authtoken.models import Token
from rest_framework import status
from .models import Source, Registration, fire_jembi_post
from .tasks import jembi_post_json, jembi_post_xml
from .tasks import Jembi_Post_Json, Jembi_Post_Xml


Jembi_Post_Json.get_timestamp = lambda x: "20130819144811"


TEST_REG_DATA = {
    "hcw_msisdn": None,
    "mom_msisdn": "+27001",
    "mom_id_type": "sa_id",
    "mom_lang": "en",
    "mom_edd": "2015-08-01",
    "mom_id_no": "8009151234001",
    "mom_dob": None,
    "clinic_code": "12345",
    "authority": "clinic"
}

TEST_SOURCE_DATA = {
    "name": "Test Source"
}


class APITestCase(TestCase):

    def setUp(self):
        self.adminclient = APIClient()
        self.normalclient = APIClient()


class AuthenticatedAPITestCase(APITestCase):

    def _replace_post_save_hooks(self):
        has_listeners = lambda: post_save.has_listeners(Registration)
        assert has_listeners(), (
            "Registration model has no post_save listeners. Make sure"
            " helpers cleaned up properly in earlier tests.")
        post_save.disconnect(fire_jembi_post, sender=Registration)
        assert not has_listeners(), (
            "Registration model still has post_save listeners. Make sure"
            " helpers cleaned up properly in earlier tests.")

    def _restore_post_save_hooks(self):
        has_listeners = lambda: post_save.has_listeners(Registration)
        assert not has_listeners(), (
            "Registration model still has post_save listeners. Make sure"
            " helpers removed them properly in earlier tests.")
        post_save.connect(fire_jembi_post, sender=Registration)

    def make_source(self, post_data=TEST_SOURCE_DATA):
        user = User.objects.get(username='testadminuser')
        post_data["user"] = "/api/v2/users/%s/" % user.id

        response = self.adminclient.post('/api/v2/sources/',
                                         json.dumps(post_data),
                                         content_type='application/json')
        return response

    def make_registration(self, post_data=TEST_REG_DATA):
        source = self.make_source()
        post_data["source"] = "/api/v2/sources/%s/" % source.data["id"]

        response = self.normalclient.post('/api/v2/registrations/',
                                          json.dumps(post_data),
                                          content_type='application/json')
        return response

    def setUp(self):
        super(AuthenticatedAPITestCase, self).setUp()
        self._replace_post_save_hooks()
        # adminclient setup
        self.adminusername = 'testadminuser'
        self.adminpassword = 'testadminpass'
        self.adminuser = User.objects.create_superuser(
            self.adminusername,
            'testadminuser@example.com',
            self.adminpassword)
        admintoken = Token.objects.create(user=self.adminuser)
        self.admintoken = admintoken.key
        self.adminclient.credentials(
            HTTP_AUTHORIZATION='Token ' + self.admintoken)
        # normalclient setup
        self.normalusername = 'testnormaluser'
        self.normalpassword = 'testnormalpass'
        self.normaluser = User.objects.create_user(
            self.normalusername,
            'testnormaluser@example.com',
            self.normalpassword)
        normaltoken = Token.objects.create(user=self.normaluser)
        self.normaltoken = normaltoken.key
        self.normalclient.credentials(
            HTTP_AUTHORIZATION='Token ' + self.normaltoken)

    def tearDown(self):
        self._restore_post_save_hooks()


class TestRegistrationsAPI(AuthenticatedAPITestCase):

    def test_create_source(self):
        response = self.make_source()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        d = Source.objects.last()
        self.assertEqual(d.name, 'Test Source')

    def test_create_source_deny_normaluser(self):
        user = User.objects.get(username='testnormaluser')
        post_data = TEST_SOURCE_DATA
        post_data["user"] = "/api/v2/users/%s/" % user.id
        response = self.normalclient.post('/api/v2/sources/',
                                          json.dumps(post_data),
                                          content_type='application/json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_registration(self):
        reg_response = self.make_registration()
        self.assertEqual(reg_response.status_code, status.HTTP_201_CREATED)

        d = Registration.objects.last()
        self.assertEqual(d.mom_id_type, 'sa_id')


class TestJembiPostJsonTask(AuthenticatedAPITestCase):

    def test_build_jembi_json(self):
        registration = self.make_registration()
        reg = Registration.objects.get(pk=registration.data["id"])
        expected_json = {
            'edd': '20150801',
            'id': '8009151234001',
            'lang': 'en',
            'dob': None,
            'dmsisdn': None,
            'mha': 1,
            'cmsisdn': '+27001',
            'faccode': '12345',
            'encdate': '20130819144811',
            'type': 3,
            'swt': 1
        }
        json = jembi_post_json.build_jembi_json(reg)
        self.assertEqual(expected_json, json)

    @responses.activate
    def test_jembi_post_json(self):
        registration = self.make_registration()

        responses.add(responses.POST,
                      "http://test/v2/json/subscription",
                      body='jembi_post_json task', status=201,
                      content_type='application/json')

        task_response = jembi_post_json.apply_async(
            kwargs={"registration_id": registration.data["id"]})
        self.assertEqual(task_response.get(), 'jembi_post_json task')


class TestJembiPostXmlTask(AuthenticatedAPITestCase):

    def test_build_jembi_xml(self):
        registration = self.make_registration()
        reg = Registration.objects.get(pk=registration.data["id"])
        result = jembi_post_xml.build_jembi_xml(reg)
        print result
        self.assertEqual(1, 2)
