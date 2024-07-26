import random
import logging
import time
from xml.etree.ElementTree import fromstring

import declxml as xml
import requests
from requests_cache import CachedSession

logger = logging.getLogger(__name__)


    
def sleep_with_backoff_and_jitter(base_time, tries=1, jitter_factor=0.5):
    """Sleep with exponential backoff and jitter."""
    sleep_time = base_time * 2 ** tries * random.uniform(1 - jitter_factor, 1 + jitter_factor)
    time.sleep(sleep_time)

class BGGClient:
    BASE_XML_URL = "https://www.boardgamegeek.com/xmlapi2"
    BASE_JSON_URL = "https://www.boardgamegeek.com/api"

    def __init__(self, cache=None, debug=False):
        if not cache:
            self.requester = requests.Session()
        else:
            self.requester = cache.cache

        if debug:
            logging.basicConfig(level=logging.DEBUG)

    def login(self, user_name, password):
        headers = {'Content-Type': 'application/json'}
        response = self.requester.post('https://boardgamegeek.com/login/api/v1', json={'credentials':{'username': user_name, 'password': password}}, headers=headers)
        if response.status_code != 204:
            raise BGGException(
                f"BGG returned status code {response.status_code} when requesting {response.url}"
            )


    def collection(self, user_name, **kwargs):
        params = kwargs.copy()
        params["username"] = user_name
        data = self._make_request_xml("/collection?version=1&showprivate=1&stats=1", params)
        collection = self._collection_to_games(data)
        return collection

    def plays(self, user_name):
        params = {
            "username": user_name,
            "page": 1,
        }
        all_plays = []

        data = self._make_request_xml("/plays?version=1", params)
        new_plays = self._plays_to_games(data)

        while (len(new_plays) > 0):
            all_plays = all_plays + new_plays
            params["page"] += 1
            data = self._make_request_xml("/plays?version=1", params)
            new_plays = self._plays_to_games(data)

        return all_plays
    
    def tags(self, game_ids):
        tag_data = {}
        for game_id in game_ids:
            data = self._make_request_json("/tags", {'objectid': game_id, 'objecttype': 'thing'})
            tag_data[game_id] = [tag['rawtag'] for tag in data['mytags']]
        return tag_data
        
    

    def game_list(self, game_ids):
        if not game_ids:
            return []

        # Split game_ids into smaller chunks to avoid "414 URI too long"
        def chunks(iterable, n):
            for i in range(0, len(iterable), n):
                yield iterable[i:i + n]

        games = []
        # 20 is the maximum for the thing API 
        # See: https://boardgamegeek.com/thread/3330409/article/44586126#44586126
        for game_ids_subset in chunks(game_ids, 20):
            url = "/thing/?stats=1&id=" + ",".join([str(id_) for id_ in game_ids_subset])
            data = self._make_request_xml(url)
            games += self._games_list_to_games(data)

        return games

    def _make_request_xml(self, url, params={}, tries=0):
        """
        Makes a request to the specified URL with the given parameters and handles XML parsing.
        Args:
            url (str): The URL to make the request to.
            params (dict, optional): The parameters to include in the request. Defaults to an empty dictionary.
            tries (int, optional): The number of times the request has been retried. Defaults to 0.
        Returns:
            str: The response text.
        Raises:
            BGGException: If the request encounters errors or the BGG API closes the connection prematurely.
        Notes:
            - This method uses exponential backoff and jitter for retrying failed requests.
            - If the request encounters HTTP errors (4xx or 5xx status codes), a `BGGException` is raised.
            - If the request encounters connection errors or chunked encoding errors, the method will retry the request up to 10 times.
            - If the request encounters a "Too Many Requests" error, the method will retry the request up to 3 times with a 30-second delay between retries.
            - If the response contains XML errors, a `BGGException` is raised with the specific error messages.
            - This method is recursive, meaning it calls itself if a retry is needed.
        """
        try:
            response = self.requester.get(BGGClient.BASE_XML_URL + url, params=params)
            response.raise_for_status()  # This will raise an exception for 4xx and 5xx status codes
        except (
            requests.exceptions.HTTPError,
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError
        ):
            if tries < 10:
                sleep_with_backoff_and_jitter(1, tries)
                return self._make_request_xml(url, params=params, tries=tries + 1)
            else:
                raise BGGException("BGG API closed the connection prematurely, please try again...")
        except requests.exceptions.TooManyRequests:
            if tries < 10:
                logger.debug("BGG returned \"Too Many Requests\", waiting 30 seconds before trying again...")
                sleep_with_backoff_and_jitter(30, tries)
                return self._make_request_xml(url, params=params, tries=tries + 1)
            else:
                raise BGGException(f"BGG returned status code {response.status_code} when requesting {response.url}")

        logger.debug("REQUEST: " + response.url)
        logger.debug("RESPONSE: \n" + prettify_if_xml(response.text))

        tree = fromstring(response.text)
        if tree.tag == "errors":
            raise BGGException(
                f"BGG returned errors while requesting {response.url} - " +
                str([subnode.text for node in tree for subnode in node])
            )

        return response.text
    
    def _make_request_json(self, url, params={}, tries=0):
        """
        Makes a request to the specified URL with the given parameters and handles JSON parsing.
        Args:
            url (str): The URL to make the request to.
            params (dict, optional): The parameters to include in the request. Defaults to an empty dictionary.
            tries (int, optional): The number of times the request has been retried. Defaults to 0.
        Returns:
            str: The response text.
        Raises:
            BGGException: If the request encounters errors or the BGG API closes the connection prematurely.
        Notes:
            - This method uses exponential backoff and jitter for retrying failed requests.
            - If the request encounters HTTP errors (4xx or 5xx status codes), a `BGGException` is raised.
            - If the request encounters connection errors or chunked encoding errors, the method will retry the request up to 10 times.
            - If the request encounters a "Too Many Requests" error, the method will retry the request up to 3 times with a 30-second delay between retries.
            - If the response contains XML errors, a `BGGException` is raised with the specific error messages.
            - This method is recursive, meaning it calls itself if a retry is needed.
        """
        try:
            response = self.requester.get(BGGClient.BASE_JSON_URL + url, params=params)
            response.raise_for_status()  # This will raise an exception for 4xx and 5xx status codes
        except (
            requests.exceptions.HTTPError,
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError
        ):
            if tries < 3:
                time.sleep(2)
                return self._make_request_json(url, params=params, tries=tries + 1)
            else:
                raise BGGException("BGG API closed the connection prematurely, please try again...")
        except requests.exceptions.TooManyRequests:
            if tries < 3:
                logger.debug("BGG returned \"Too Many Requests\", waiting 30 seconds before trying again...")
                time.sleep(30)
                return self._make_request_json(url, params=params, tries=tries + 1)
            else:
                raise BGGException(f"BGG returned status code {response.status_code} when requesting {response.url}")

        logger.debug("REQUEST: " + response.url)
        logger.debug("RESPONSE: \n" + response.text)

        return response.json()

    def _plays_to_games(self, data):
        def after_players_hook(_, status):
            return status["name"] if "name" in status else "Unknown"

        plays_processor = xml.dictionary("plays", [
            xml.array(
                xml.dictionary('play', [
                    xml.integer(".", attribute="id", alias="playid"),
                    xml.dictionary('item', [
                        xml.string(".", attribute="name", alias="gamename"),
                        xml.integer(".", attribute="objectid", alias="gameid")
                    ], alias='game'),
                    xml.array(
                        xml.dictionary('players/player', [
                            xml.string(".", attribute="name", required=False, default="Unknown")
                        ], required=False, alias='players', hooks=xml.Hooks(after_parse=after_players_hook))
                    )

                ], required=False, alias="plays")
            )
        ])

        plays = xml.parse_from_string(plays_processor, data)
        plays = plays["plays"]
        return plays

    def _collection_to_games(self, data):
        def after_status_hook(_, status):
            return [tag for tag, value in status.items() if value == "1"]

        game_in_collection_processor = xml.dictionary("items", [
            xml.array(
                xml.dictionary('item', [
                    xml.integer(".", attribute="objectid", alias="id"),
                    xml.string("name"),
                    xml.string("image", required=False, alias="image"),
                    xml.string("thumbnail", required=False, alias="thumbnail"),
                    xml.string("version/item/image", required=False, alias="image_version"),
                    xml.string("stats/rating", required=False, attribute="value", alias="personal_rating"),
                    xml.dictionary("status", [
                        xml.string(".", attribute="fortrade"),
                        xml.string(".", attribute="own"),
                        xml.string(".", attribute="preordered"),
                        xml.string(".", attribute="prevowned"),
                        xml.string(".", attribute="want"),
                        xml.string(".", attribute="wanttobuy"),
                        xml.string(".", attribute="wanttoplay"),
                        xml.string(".", attribute="wishlist"),
                    ], alias='collectionstatus', hooks=xml.Hooks(after_parse=after_status_hook)),
                    xml.string("privateinfo", attribute="acquisitiondate", required=False, alias="lastmodified"),
                    xml.integer("numplays"),
                ], required=False, alias="items"),
            )
        ])
        collection = xml.parse_from_string(game_in_collection_processor, data)
        collection = collection["items"]
        return collection

    def _games_list_to_games(self, data):
        def numplayers_to_result(_, results):
            result = {result["value"].lower().replace(" ", "_"): int(result["numvotes"]) for result in results}

            if not result:
                result = {'best': 0, 'recommended': 0, 'not_recommended': 0}

            is_recommended = result['best'] + result['recommended'] > result['not_recommended']
            if not is_recommended:
                return "not_recommended"

            is_best = result['best'] > 10 and result['best'] > result['recommended']
            if is_best:
                return "best"

            return "recommended"

        def suggested_numplayers(_, numplayers):
            # Remove not_recommended player counts and counts like "2+"
            numplayers = [players for players in numplayers if not players["result"].endswith('+')]

            # If there's only one player count, that's the best one
            if len(numplayers) == 1:
                numplayers[0]["result"] = "best"

            # Just return the numbers
            return [
                (players["numplayers"], players["result"])
                for players in numplayers
            ]

        def log_item(_, item):
            logger.debug("Successfully parsed: {} (id: {}).".format(item["name"], item["id"]))
            return item

        game_processor = xml.dictionary("items", [
            xml.array(
                xml.dictionary(
                    "item",
                    [
                        xml.integer(".", attribute="id"),
                        xml.string(".", attribute="type"),
                        xml.string("name[@type='primary']", attribute="value", alias="name"),
                        xml.string("description"),
                        xml.array(
                            xml.string(
                                "link[@type='boardgamecategory']",
                                attribute="value",
                                required=False
                            ),
                            alias="categories",
                        ),
                        xml.array(
                            xml.string(
                                "link[@type='boardgamemechanic']",
                                attribute="value",
                                required=False
                            ),
                            alias="mechanics",
                        ),
                        xml.array(
                            xml.dictionary(
                                "link[@type='boardgameexpansion']", [
                                    xml.integer(".", attribute="id"),
                                    xml.boolean(".", attribute="inbound", required=False),
                                ],
                                required=False
                            ),
                            alias="expansions",
                        ),
                        xml.array(
                            xml.dictionary("poll[@name='suggested_numplayers']/results", [
                                xml.string(".", attribute="numplayers"),
                                xml.array(
                                    xml.dictionary("result", [
                                        xml.string(".", attribute="value"),
                                        xml.integer(".", attribute="numvotes"),
                                    ], required=False),
                                    hooks=xml.Hooks(after_parse=numplayers_to_result)
                                )
                            ]),
                            alias="suggested_numplayers",
                            hooks=xml.Hooks(after_parse=suggested_numplayers),
                        ),
                        xml.string(
                            "statistics/ratings/averageweight",
                            attribute="value",
                            alias="weight"
                        ),
                        xml.string(
                            "statistics/ratings/ranks/rank[@friendlyname='Board Game Rank']",
                            attribute="value",
                            required=False,
                            alias="rank"
                        ),
                        xml.string(
                            "statistics/ratings/usersrated",
                            attribute="value",
                            alias="usersrated"
                        ),
                        xml.string(
                            "statistics/ratings/owned",
                            attribute="value",
                            alias="numowned"
                        ),
                        xml.string(
                            "statistics/ratings/bayesaverage",
                            attribute="value",
                            alias="rating"
                        ),
                        xml.string("playingtime", attribute="value", alias="playing_time"),
                    ],
                    required=False,
                    alias="items",
                    hooks=xml.Hooks(after_parse=log_item),
                )
            )
        ])
        games = xml.parse_from_string(game_processor, data)
        games = games["items"]
        return games

class CacheBackendSqlite:
    def __init__(self, path, ttl):
        self.cache = CachedSession(
            cache_name=path,
            backend="sqlite",
            expire_after=ttl,
            extension="",
            fast_save=True,
            allowable_codes=(200,)
        )

class BGGException(Exception):
    pass

def prettify_if_xml(xml_string):
    import xml.dom.minidom
    import re
    xml_string = re.sub(r"\s+<", "<", re.sub(r">\s+", ">", re.sub(r"\s+", " ", xml_string)))
    if not xml_string.startswith("<?xml"):
        return xml_string

    parsed = xml.dom.minidom.parseString(xml_string)
    return parsed.toprettyxml()
