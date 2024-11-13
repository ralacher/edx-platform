"""
Utils function for notifications app
"""
from typing import Dict, List

from common.djangoapps.student.models import CourseAccessRole, CourseEnrollment
from openedx.core.djangoapps.discussions.models import DEFAULT_CONFIG_ENABLED
from openedx.core.djangoapps.django_comment_common.models import Role
from openedx.core.djangoapps.notifications.config.waffle import ENABLE_NOTIFICATIONS, ENABLE_NEW_NOTIFICATION_VIEW
from openedx.core.lib.cache_utils import request_cached


def find_app_in_normalized_apps(app_name, apps_list):
    """
    Returns app preference based on app_name
    """
    for app in apps_list:
        if app.get('name') == app_name:
            return app
    return None


def find_pref_in_normalized_prefs(pref_name, app_name, prefs_list):
    """
    Returns preference based on preference_name and app_name
    """
    for pref in prefs_list:
        if pref.get('name') == pref_name and pref.get('app_name') == app_name:
            return pref
    return None


def get_show_notifications_tray(user):
    """
    Returns show_notifications_tray as boolean for the courses in which user is enrolled
    """
    show_notifications_tray = False
    learner_enrollments_course_ids = CourseEnrollment.objects.filter(
        user=user,
        is_active=True
    ).values_list('course_id', flat=True)

    for course_id in learner_enrollments_course_ids:
        if ENABLE_NOTIFICATIONS.is_enabled(course_id):
            show_notifications_tray = True
            break

    return show_notifications_tray


def get_is_new_notification_view_enabled():
    """
    Returns True if the waffle flag for the new notification view is enabled, False otherwise.
    """
    return ENABLE_NEW_NOTIFICATION_VIEW.is_enabled()


def get_list_in_batches(input_list, batch_size):
    """
    Divides the list of objects into list of list of objects each of length batch_size.
    """
    list_length = len(input_list)
    for index in range(0, list_length, batch_size):
        yield input_list[index: index + batch_size]


def get_user_forum_roles(user_id: int, course_id: str) -> List[str]:
    """
    Get forum roles for the given user in the specified course.

    :param user_id: User ID
    :param course_id: Course ID
    :return: List of forum roles
    """
    return list(Role.objects.filter(course_id=course_id, users__id=user_id).values_list('name', flat=True))


@request_cached()
def get_notification_types_with_visibility_settings() -> Dict[str, List[str]]:
    """
    Get notification types with their visibility settings.

    :return: List of dictionaries with notification type names and corresponding visibility settings
    """
    from .base_notification import COURSE_NOTIFICATION_TYPES

    notification_types_with_visibility_settings = {}
    for notification_type in COURSE_NOTIFICATION_TYPES.values():
        if notification_type.get('visible_to'):
            notification_types_with_visibility_settings[notification_type['name']] = notification_type['visible_to']

    return notification_types_with_visibility_settings


def filter_out_visible_notifications(
    user_preferences: dict,
    notifications_with_visibility: Dict[str, List[str]],
    user_forum_roles: List[str],
    user_course_roles: List[str]
) -> dict:
    """
    Filter out notifications visible to forum roles from user preferences.

    :param user_preferences: User preferences dictionary
    :param notifications_with_visibility: List of dictionaries with notification type names and
    corresponding visibility settings
    :param user_forum_roles: List of forum roles for the user
    :param user_course_roles: List of course roles for the user
    :return: Updated user preferences dictionary
    """
    for user_preferences_app, app_config in user_preferences.items():
        if 'notification_types' in app_config:
            # Iterate over the types to remove and pop them from the dictionary
            for notification_type, is_visible_to in notifications_with_visibility.items():
                is_visible = False
                for role in is_visible_to:
                    if role in user_forum_roles or role in user_course_roles:
                        is_visible = True
                        break
                if is_visible:
                    continue
                if notification_type in user_preferences[user_preferences_app]['notification_types']:
                    user_preferences[user_preferences_app]['notification_types'].pop(notification_type)
    return user_preferences


def remove_preferences_with_no_access(preferences: dict, user) -> dict:
    """
    Filter out notifications visible to forum roles from user preferences.

    :param preferences: User preferences dictionary
    :param user: User object
    :return: Updated user preferences dictionary
    """
    user_preferences = preferences['notification_preference_config']
    user_forum_roles = get_user_forum_roles(user.id, preferences['course_id'])
    notifications_with_visibility_settings = get_notification_types_with_visibility_settings()
    user_course_roles = CourseAccessRole.objects.filter(
        user=user,
        course_id=preferences['course_id']
    ).values_list('role', flat=True)
    preferences['notification_preference_config'] = filter_out_visible_notifications(
        user_preferences,
        notifications_with_visibility_settings,
        user_forum_roles,
        user_course_roles
    )
    return preferences


def clean_arguments(kwargs):
    """
    Returns query arguments from command line arguments
    """
    clean_kwargs = {}
    for key in ['app_name', 'notification_type', 'course_id']:
        if kwargs.get(key):
            clean_kwargs[key] = kwargs[key]
    if kwargs.get('created', {}):
        clean_kwargs.update(kwargs.get('created'))
    return clean_kwargs


def aggregate_notification_configs(default_config, configs_list):
    """
    Update default notification config with values from other configs.
    Rules:
    1. Start with default config as base
    2. If any value is True in other configs, make it True
    3. All email_cadence will be set to "Daily"

    Args:
        default_config (dict): Base configuration to start with
        configs_list (list): List of notification config dictionaries to apply

    Returns:
        dict: Updated config following the same structure
    """
    if not configs_list:
        return default_config

    # Create a deep copy of default config to avoid modifying the original
    import copy
    result_config = copy.deepcopy(default_config)

    # Get all categories from default config
    categories = result_config.keys()

    # Process each category
    for category in categories:
        category_config = result_config[category]

        # Process each config in the list
        for config in configs_list:

            if category not in config:
                continue

            other_cat_config = config[category]

            # Update enabled status
            category_config["enabled"] |= other_cat_config.get("enabled", False)

            # Update core_notification_types
            if "core_notification_types" in other_cat_config:
                existing_types = set(category_config.get("core_notification_types", []))
                existing_types.update(other_cat_config["core_notification_types"])
                category_config["core_notification_types"] = list(existing_types)

            # Update notification types
            if "notification_types" in other_cat_config:
                for type_key, type_config in other_cat_config["notification_types"].items():
                    # Only process notification types that exist in default config
                    if type_key in category_config["notification_types"]:
                        for field in ["web", "push", "email"]:
                            if field in type_config:
                                category_config["notification_types"][type_key][field] |= type_config[field]
                        # Set email_cadence to Daily
                        category_config["notification_types"][type_key]["email_cadence"] = \
                        default_config[category]["notification_types"][type_key]["email_cadence"]

    return result_config
