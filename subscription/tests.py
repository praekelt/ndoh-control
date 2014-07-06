"""
Tests for Subscription Application 
"""
from tastypie.test import ResourceTestCase
from django.test import TestCase
from django.contrib.auth.models import User
from django.core.urlresolvers import reverse
from subscription.models import MessageSet, Message
from subscription.tasks import ingest_csv
from StringIO import StringIO
import json


class SubscriptionResourceTest(ResourceTestCase):

    def setUp(self):
        super(SubscriptionResourceTest, self).setUp()

        # Create a user.
        self.username = 'testuser'
        self.password = 'testpass'
        self.user = User.objects.create_user(self.username,
            'testuser@example.com', self.password)
        self.api_key = self.user.api_key.key

    def get_credentials(self):
        return self.create_apikey(self.username, self.api_key)

    def test_get_list_unauthorzied(self):
        self.assertHttpUnauthorized(self.api_client.get('/api/v1/subscription/', format='json'))

    def test_api_keys_created(self):
        self.assertEqual(True, self.api_key is not None) 

    def test_get_list_json(self):
        resp = self.api_client.get('/api/v1/subscription/', format='json', authentication=self.get_credentials())
        self.assertValidJSONResponse(resp)

        # Scope out the data for correctness.
        self.assertEqual(len(self.deserialize(resp)['objects']), 0)

    def test_post_subscription_with_non_existent_schedule_ref(self):
        data = {
            "active": True,
            "completed": False,
            "contact_key": "82309423098",
            "lang": "en",
            "next_sequence_number": 1,
            "resource_uri": "/api/v1/subscription/1/",
            "schedule": "/api/v1/periodic_task/10/", # Non existent task
            "to_addr": "+271234",
            "user_account": "80493284823"
        }

        response = self.api_client.post('/api/v1/subscription/', format='json',
                                        authentication=self.get_credentials(),
                                        data=data)
        json_item = json.loads(response.content)
        self.assertHttpBadRequest(response)
        self.assertEqual("Could not find the provided object via resource URI " \
                         "'/api/v1/periodic_task/10/'.", json_item["error"])

    def test_post_subscription_good(self):
        data = {
            "contact_key": "82309423098",
            "lang": "en",
            "message_set": "/api/v1/message_set/3/",
            "next_sequence_number": 1,
            "resource_uri": "/api/v1/subscription/1/",
            "schedule": "/api/v1/periodic_task/1/",
            "to_addr": "+271234",
            "user_account": "80493284823"
        }

        response = self.api_client.post('/api/v1/subscription/', format='json',
                                        authentication=self.get_credentials(),
                                        data=data)
        json_item = json.loads(response.content)
        self.assertEqual("82309423098", json_item["contact_key"])
        self.assertEqual(True, json_item["active"])
        self.assertEqual(False, json_item["completed"])
        self.assertEqual("en", json_item["lang"])
        self.assertEqual("/api/v1/message_set/3/", json_item["message_set"])
        self.assertEqual(1, json_item["next_sequence_number"])
        self.assertEqual("/api/v1/periodic_task/1/", json_item["schedule"])
        self.assertEqual("+271234", json_item["to_addr"])
        self.assertEqual("80493284823", json_item["user_account"])

class TestUploadCSV(TestCase):

    MSG_HEADER = (
        "message_id,en,safe,af,safe,zu,safe,xh,safe,ve,safe,tn,safe,ts,safe,ss,safe,st,safe,nso,safe,nr,safe\r\n")
    MSG_LINE_CLEAN_1 = (
        "1,hello,0,hello1,0,hell2,0,,0,,0,,0,,0,,0,,0,,0,hello3,0\r\n")
    MSG_LINE_CLEAN_2 = (
        "2,goodbye,0,goodbye1,0,goodbye2,0,,0,,0,,0,,0,,0,,0,,0,goodbye3,0\r\n")
    MSG_LINE_DIRTY_1= (
        "A,sequence_number_is_text,0,goodbye1,0,goodbye2,0,,0,,0,,0,,0,,0,,0,,0,goodbye3,0\r\n")


    def setUp(self):
        self.admin = User.objects.create_superuser(
            'test', 'test@example.com', "pass123")

    def test_upload_view_not_logged_in_blocked(self):
        response = self.client.get(reverse("csv_uploader"))
        self.assertEqual(response.template_name, "admin/login.html")

    def test_upload_view_logged_in(self):
        self.client.login(username="test", password="pass123")

        response = self.client.get(reverse("csv_uploader"))
        self.assertIn("Upload CSV", response.content)

    def test_upload_csv_clean(self):
        message_set = MessageSet.objects.get(short_name="standard")
        clean_sample = self.MSG_HEADER + \
            self.MSG_LINE_CLEAN_1 + self.MSG_LINE_CLEAN_2
        uploaded = StringIO(clean_sample)
        ingest_csv(uploaded, message_set)
        imported_en = Message.objects.filter(sequence_number="1", lang="en")[0]
        self.assertEquals(imported_en.content, "hello")
        imported_af = Message.objects.filter(sequence_number="1", lang="af")[0]
        self.assertEquals(imported_af.content, "hello1")
        imported_nr = Message.objects.filter(sequence_number="1", lang="nr")[0]
        self.assertEquals(imported_nr.content, "hello3")
        imported_en = Message.objects.filter(sequence_number="2", lang="en")[0]
        self.assertEquals(imported_en.content, "goodbye")
        imported_af2 = Message.objects.filter(sequence_number="2", lang="af")[0]
        self.assertEquals(imported_af2.content, "goodbye1")
        imported_nr2 = Message.objects.filter(sequence_number="2", lang="nr")[0]
        self.assertEquals(imported_nr2.content, "goodbye3")

    def test_upload_csv_dirty(self):
        message_set = MessageSet.objects.get(short_name="standard")
        dirty_sample = self.MSG_HEADER + \
            self.MSG_LINE_CLEAN_1 + self.MSG_LINE_DIRTY_1
        uploaded = StringIO(dirty_sample)
        ingest_csv(uploaded, message_set)
        imported_en = Message.objects.filter(sequence_number="1", lang="en")[0]
        self.assertEquals(imported_en.content, "hello")
        imported_en_dirty = Message.objects.filter(lang="en")
        self.assertEquals(len(imported_en_dirty), 1)


    
