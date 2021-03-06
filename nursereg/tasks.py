import requests
import json
from requests.exceptions import HTTPError
from datetime import datetime
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from celery import task
from celery.utils.log import get_task_logger
from celery.exceptions import SoftTimeLimitExceeded
from djcelery.models import PeriodicTask
from go_http import HttpApiSender
from go_http.contacts import ContactsApiClient
from .models import NurseReg
from subscription.models import Subscription, MessageSet

logger = get_task_logger(__name__)


def get_registrant_id(id_type, id_no=None, passport_origin=None, msisdn=None):
    if id_type == 'sa_id':
        return id_no + "^^^ZAF^NI"
    elif id_type == 'passport':
        return id_no + '^^^' + passport_origin.upper() + '^PPN'
    else:
        return msisdn.replace('+', '') + '^^^ZAF^TEL'


def get_subscription_type(authority):
    authority_map = {
        'personal': 1,
        'chw': 2,
        'clinic': 3
    }
    return authority_map[authority]


def get_today():
    return datetime.today()


def get_timestamp():
    return get_today().strftime("%Y%m%d%H%M%S")


def get_dob(mom_dob):
    if mom_dob is not None:
        return mom_dob.strftime("%Y%m%d")
    else:
        return None


def get_persal(persal):
    if persal is not None:
        return str(persal)
    else:
        return None


def get_sanc(sanc):
    if sanc is not None:
        return str(sanc)
    else:
        return None


def get_sender():
    sender = HttpApiSender(
        account_key=settings.VUMI_GO_ACCOUNT_KEY,
        conversation_key=settings.VUMI_GO_CONVERSATION_KEY,
        conversation_token=settings.VUMI_GO_ACCOUNT_TOKEN
    )
    return sender


def build_jembi_json(nursereg):
    """ Compile json to be sent to Jembi. """
    json_template = {
        "mha": 1,
        "swt": 3,
        "type": 7,
        "dmsisdn": nursereg.dmsisdn,
        "cmsisdn": nursereg.cmsisdn,
        "rmsisdn": nursereg.rmsisdn,
        "faccode": nursereg.faccode,
        "id": get_registrant_id(
            nursereg.id_type, nursereg.id_no, nursereg.passport_origin,
            nursereg.cmsisdn),
        "dob": get_dob(nursereg.dob),
        "persal": get_persal(nursereg.persal_no),
        "sanc": get_sanc(nursereg.sanc_reg_no),
        "encdate": get_timestamp()
    }

    return json_template


@task(time_limit=10, ignore_result=True)
def jembi_post_json(nursereg_id, sender=None):
    """ Task to send nurse registrations Json to Jembi"""

    logger.info("Compiling Jembi Json data")
    try:
        nursereg = NurseReg.objects.get(pk=nursereg_id)
        json_doc = build_jembi_json(nursereg)

        try:
            result = requests.post(
                "%s/nc/subscription" % settings.JEMBI_BASE_URL,  # url
                headers={'Content-Type': 'application/json'},
                data=json.dumps(json_doc),
                auth=(settings.JEMBI_USERNAME, settings.JEMBI_PASSWORD),
                verify=False
            )
            result.raise_for_status()
            vumi_fire_metric.apply_async(
                kwargs={
                    "metric": u"%s.%s.sum.json_to_jembi_success" % (
                        settings.METRIC_ENV, 'nursereg'),
                    "value": 1,
                    "agg": "sum",
                    "sender": sender}
            )
        except HTTPError as e:
            # retry message sending if in 500 range (3 default retries)
            if 500 < e.response.status_code < 599:
                if jembi_post_json.max_retries == \
                   jembi_post_json.request.retries:
                    vumi_fire_metric.apply_async(
                        kwargs={
                            "metric": u"%s.%s.sum.json_to_jembi_fail" % (
                                settings.METRIC_ENV, 'nursereg'),
                            "value": 1,
                            "agg": "sum",
                            "sender": None}
                    )
                raise jembi_post_json.retry(exc=e)
            else:
                vumi_fire_metric.apply_async(
                    kwargs={
                        "metric": u"%s.%s.sum.json_to_jembi_fail" % (
                            settings.METRIC_ENV, 'nursereg'),
                        "value": 1,
                        "agg": "sum",
                        "sender": None}
                )
                raise e
        except:
            logger.error('Problem posting JSON to Jembi', exc_info=True)
        return result.text

    except ObjectDoesNotExist:
        logger.error('Missing NurseReg object', exc_info=True)

    except SoftTimeLimitExceeded:
        logger.error(
            'Soft time limit exceeded processing Jembi send via Celery.',
            exc_info=True)


def get_client():
    return ContactsApiClient(auth_token=settings.VUMI_GO_API_TOKEN)


def define_extras_subscription(_extras, subscription):
    # Set up the new extras
    _extras[u"nc_subscription_type"] = str(subscription.message_set.id)
    _extras[u"nc_subscription_rate"] = str(subscription.schedule.id)
    _extras[u"nc_subscription_seq_start"] = str(
        subscription.next_sequence_number)
    return _extras


def define_extras_registration(_extras, nursereg):
    # Set up the new extras
    _extras[u"nc_source_name"] = nursereg.nurse_source.name
    _extras[u"nc_last_reg_id"] = str(nursereg.id)
    # Duplication of JS extras required for external nurseregs
    _extras[u"nc_faccode"] = nursereg.faccode
    _extras[u"nc_is_registered"] = "true"
    if nursereg.id_type == "sa_id":
        _extras[u"nc_id_type"] = nursereg.id_type
        _extras[u"nc_sa_id_no"] = nursereg.id_no
        _extras[u"nc_dob"] = nursereg.dob.strftime("%Y-%m-%d")
    elif nursereg.id_type == "passport":
        _extras[u"nc_id_type"] = nursereg.id_type
        _extras[u"nc_passport_num"] = nursereg.id_no
        _extras[u"nc_passport_country"] = nursereg.passport_origin
        _extras[u"nc_dob"] = nursereg.dob.strftime("%Y-%m-%d")
    if nursereg.cmsisdn != nursereg.dmsisdn:
        _extras[u"nc_registered_by"] = nursereg.dmsisdn
    if nursereg.persal_no:
        _extras[u"nc_persal"] = str(nursereg.persal_no)
    if nursereg.sanc_reg_no:
        _extras[u"nc_sanc"] = str(nursereg.sanc_reg_no)

    return _extras


def update_contact_registration(contact, nursereg, client):
    # Setup new values - only extras need updating
    existing_extras = contact["extra"]
    _extras = define_extras_registration(existing_extras, nursereg)
    update_data = {u"extra": _extras}
    return client.update_contact(contact["key"], update_data)


def update_contact_subscription(contact, subscription, client):
    # Setup new values - only extras need updating
    existing_extras = contact["extra"]
    _extras = define_extras_subscription(existing_extras, subscription)
    update_data = {u"extra": _extras}
    return client.update_contact(contact["key"], update_data)


def get_subscription_details():
    msg_set = "nurseconnect"
    sub_rate = "three_per_week"
    seq_start = 1
    return msg_set, sub_rate, seq_start


def create_subscription(contact, sender=None):
    """ Create new Control messaging subscription"""

    logger.info("Creating new Control messaging subscription")
    try:
        sub_details = get_subscription_details()
        subscription = Subscription(
            contact_key=contact["key"],
            to_addr=contact["msisdn"],
            user_account=contact["user_account"],
            lang="en",
            message_set=MessageSet.objects.get(short_name=sub_details[0]),
            schedule=PeriodicTask.objects.get(
                id=settings.SUBSCRIPTION_RATES[sub_details[1]]),
            next_sequence_number=sub_details[2],
        )
        subscription.save()
        logger.info("Created subscription for %s" % subscription.to_addr)

        vumi_fire_metric.apply_async(
            kwargs={
                "metric": u"%s.sum.nc_subscriptions" % (
                    settings.METRIC_ENV),
                "value": 1,
                "agg": "sum",
                "sender": sender}
        )
        vumi_fire_metric.apply_async(
            kwargs={
                "metric": u"%s.%s.sum.nc_subscription_to_protocol_success" % (
                    settings.METRIC_ENV, "nurseconnect"),
                "value": 1,
                "agg": "sum",
                "sender": sender}
        )

        return subscription

    except:
        vumi_fire_metric.apply_async(
            kwargs={
                "metric": u"%s.%s.sum.nc_subscription_to_protocol_fail" % (
                    settings.METRIC_ENV, "nurseconnect"),
                "value": 1,
                "agg": "sum",
                "sender": sender}
        )
        logger.error(
            'Error creating Subscription instance',
            exc_info=True)


def transfer_subscription(contact, subscription):
    # activate the same subscription on the new msisdn
    new_sub = Subscription(
        contact_key=contact["key"],
        to_addr=contact["msisdn"],
        user_account=contact["user_account"],
        lang="en",
        message_set=subscription.message_set,
        schedule=subscription.schedule,
        next_sequence_number=subscription.next_sequence_number)
    new_sub.save()

    # deactivate active subscriptions for rmsisdn
    subscription.active = False
    subscription.save()

    # TODO #123: Clear extras for old contact for external change requests

    return new_sub


def create_contact(nursereg, client):
    contact_data = {
        u"msisdn": nursereg.cmsisdn
    }
    _extras = define_extras_registration({}, nursereg)
    contact_data[u"extra"] = _extras
    return client.create_contact(contact_data)


@task(time_limit=10, ignore_result=True)
def update_create_vumi_contact(nursereg_id, client=None, sender=None):
    """ Task to update or create a Vumi contact when a nurse
        registration is created.
        Creates a nurseconnect subscription for the contact.
    """
    logger.info("Creating / Updating Contact")
    try:
        if client is None:
            client = get_client()

        # Load the nursereg
        try:
            nursereg = NurseReg.objects.get(pk=nursereg_id)

            try:
                # Get and update the contact if it exists
                contact = client.get_contact(
                    msisdn=nursereg.cmsisdn)

                logger.info("Contact exists - updating contact")
                contact = update_contact_registration(
                    contact, nursereg, client)

            # This exception should rather look for a 404 if the contact is
            # not found, but currently a 400 Bad Request is returned.
            except HTTPError as e:
                if e.response.status_code == 400:
                    # Create the contact as it doesn't exist
                    logger.info("Contact doesn't exist - creating new contact")
                    contact = create_contact(nursereg, client)

                elif 500 < e.response.status_code < 599:
                    # Retry task if 500 error
                    raise update_create_vumi_contact.retry(exc=e)
                else:
                    raise e
            except:
                logger.error('Problem contacting http_api', exc_info=True)

            # Warning: This only caters for singular messageset 'nurseconnect'
            cmsisdn_active_subs = Subscription.objects.filter(
                to_addr=nursereg.cmsisdn, active=True,
                message_set__short_name="nurseconnect")
            if cmsisdn_active_subs.count() > 0:
                # Do nothing if the cmsisdn has an active subscription
                return contact
            else:
                try:
                    # Get the old contact active subscription
                    rmsisdn_active_sub = Subscription.objects.get(
                        to_addr=nursereg.rmsisdn, active=True,
                        message_set__short_name="nurseconnect")
                    subscription = transfer_subscription(
                        contact, rmsisdn_active_sub)
                except ObjectDoesNotExist:
                    # Create new subscription for the contact
                    subscription = create_subscription(contact, sender)

                # Update the contact with subscription details
                updated_contact = update_contact_subscription(
                    contact, subscription, client)
                return updated_contact

        except ObjectDoesNotExist:
            logger.error('Missing NurseReg object', exc_info=True)

    except SoftTimeLimitExceeded:
        logger.error(
            'Soft time limit exceeded processing Jembi send via Celery.',
            exc_info=True)


@task(time_limit=10, ignore_result=True)
def fire_new_clinic_metric(client=None, sender=None):
    """ Task to increment the unique clinic nurse registrations metric.
    """

    logger.info("Firing metric")
    try:
        if client is None:
            client = get_client()

        vumi_fire_metric.apply_async(kwargs={
            "metric": u"%s.nurseconnect.unique.clinics" % (
                settings.METRIC_ENV),
            "value": 1, "agg": "sum", "sender": sender}
        )
    except SoftTimeLimitExceeded:
        logger.error(
            'Soft time limit exceeded processing Jembi send via Celery.',
            exc_info=True)
    return


@task(ignore_result=True)
def vumi_fire_metric(metric, value, agg, sender=None):
    try:
        if sender is None:
            sender = get_sender()
        sender.fire_metric(metric, value, agg=agg)
        return sender
    except SoftTimeLimitExceeded:
        logger.error(
            'Soft time limit exceed processing metric fire to Vumi HTTP API '
            'via Celery',
            exc_info=True)
