import click
from clickclick import error, info, UrlType
import keyring
import os
import stups_cli.config
import time
import tokens
import requests
import yaml

from .config import KEYRING_KEY, TOKENS_FILE_PATH

TOKEN_MINIMUM_VALIDITY_SECONDS = 60*5  # 5 minutes


class ServerError(Exception):
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return 'Server error: {}'.format(self.message)


class AuthenticationFailed(ServerError):
    def __str__(self):
        return 'Authentication failed: {}'.format(self.message)


class ConfigurationError(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return 'Configuration error: {}'.format(self.msg)


def get_config():
    return stups_cli.config.load_config('zign')


def get_tokens():
    try:
        with open(TOKENS_FILE_PATH) as fd:
            data = yaml.safe_load(fd)
    except:
        data = None
    return data or {}


def get_new_token(realm, scope, user, password, url=None, insecure=False):
    if not url:
        config = get_config()
        url = config.get('url')
    params = {'json': 'true'}
    if realm:
        params['realm'] = realm
    if scope:
        params['scope'] = ' '.join(filter(is_user_scope, scope))
    response = requests.get(url, params=params, auth=(user, password), verify=not insecure)
    if response.status_code == 401:
        raise AuthenticationFailed('Token Service returned {}'.format(response.text))
    elif response.status_code != 200:
        raise ServerError('Token Service returned HTTP status {}: {}'.format(response.status_code, response.text))
    try:
        json_data = response.json()
    except:
        raise ServerError('Token Service returned invalid JSON data')

    if not json_data.get('access_token'):
        raise ServerError('Token Service returned invalid JSON (access_token missing)')
    return json_data

get_new_token.__annotations__ = {'realm': str, 'scope': list}


def get_existing_token(name):
    """Return existing token if it exists and if it's valid, return None otherwise"""
    data = get_tokens()
    existing_token = data.get(name)
    if is_valid(existing_token):
        return existing_token

get_existing_token.__annotations__ = {'name': str, 'return': dict}


def store_token(name, result):
    data = get_tokens()

    data[name] = result
    data[name]['creation_time'] = time.time()

    dir_path = os.path.dirname(TOKENS_FILE_PATH)
    if dir_path:
        try:
            os.makedirs(dir_path)
        except OSError:
            pass

    with open(TOKENS_FILE_PATH, 'w') as fd:
        yaml.safe_dump(data, fd)

store_token.__annotations__ = {'name': str, 'result': dict}


def get_named_token(scope, realm, name, user, password, url=None,
                    insecure=False, refresh=False, use_keyring=True, prompt=False):
    '''get named access token, return existing if still valid'''

    if name and not refresh:
        existing_token = get_existing_token(name)
        if existing_token:
            return existing_token

    if name and not realm:
        access_token = get_service_token(name, scope)
        if access_token:
            return {'access_token': access_token}

    config = get_config()

    url = url or config.get('url')

    while not url and prompt:
        url = click.prompt('Please enter the OAuth access token service URL', type=UrlType())

        try:
            requests.get(url, timeout=5, verify=not insecure)
        except:
            error('Could not reach {}'.format(url))
            url = None

        config['url'] = url

    stups_cli.config.store_config(config, 'zign')

    password = password or keyring.get_password(KEYRING_KEY, user)

    while True:
        if not password and prompt:
            password = click.prompt('Password for {}'.format(user), hide_input=True)

        try:
            result = get_new_token(realm, scope, user, password, url=url, insecure=insecure)
            break
        except AuthenticationFailed as e:
            if prompt:
                error(str(e))
                info('Please check your username and password and try again.')
                password = None
            else:
                raise

    if result and use_keyring:
        keyring.set_password(KEYRING_KEY, user, password)

    if name:
        store_token(name, result)

    return result


def is_valid(token):
    now = time.time()
    return token and now < (token.get('creation_time', 0) + token.get('expires_in', 0) - TOKEN_MINIMUM_VALIDITY_SECONDS)

is_valid.__annotations__ = {'token': dict}


def is_user_scope(scope):
    """Is the given scope supported for users (employees) in Token Service?"""
    return scope in set(['uid', 'cn'])
is_user_scope.__annotations__ = {'scope': str}


def get_service_token(name, scopes):
    """Get service token (tokens lib) if possible, otherwise return None"""
    tokens.manage(name, scopes)
    try:
        access_token = tokens.get(name)
    except tokens.ConfigurationError:
        # will be thrown if configuration is missing (e.g. OAUTH2_ACCESS_TOKEN_URL)
        access_token = None
    except tokens.InvalidCredentialsError:
        # will be thrown if $CREDENTIALS_DIR/*.json cannot be read
        access_token = None

    return access_token

get_service_token.__annotations__ = {'name': str, 'scopes': list}


def get_token(name, scopes):
    """Get an OAuth token, either from Token Service
    or directly from OAuth provider (using the Python tokens library)"""

    # first try if a token exists already
    token = get_existing_token(name)

    if token:
        return token['access_token']

    access_token = get_service_token(name, scopes)
    if access_token:
        return access_token

    config = get_config()
    user = config.get('user') or os.getenv('ZIGN_USER') or os.getenv('USER')

    if not user:
        raise ConfigurationError('Missing OAuth username. ' +
                                 'Either set "user" in configuration file or ZIGN_USER environment variable.')

    if not config.get('url'):
        raise ConfigurationError('Missing OAuth access token service URL. ' +
                                 'Please set "url" in configuration file.')

    password = os.getenv('ZIGN_PASSWORD') or keyring.get_password(KEYRING_KEY, user)
    token = get_new_token(config.get('realm'), scopes, user, password,
                          url=config.get('url'), insecure=config.get('insecure'))
    if token:
        store_token(name, token)
        return token['access_token']

get_token.__annotations__ = {'name': str, 'scopes': list}
