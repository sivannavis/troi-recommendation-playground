from datetime import datetime

import click

from troi import Playlist
from troi.musicbrainz.recording import RecordingListElement
from troi.playlist import PlaylistRedundancyReducerElement, PlaylistMakerElement, PlaylistShuffleElement
import troi.listenbrainz.recs
import troi.listenbrainz.listens
import troi.filters
import troi.musicbrainz.recording_lookup


@click.group()
def cli():
    pass


DAYS_OF_RECENT_LISTENS_TO_EXCLUDE = 60  # Exclude tracks listened in last X days from the daily jams playlist
DAILY_JAMS_MIN_RECORDINGS = 25  # the minimum number of recordings we aspire to have in a daily jam, this is not a hard limit


class DailyJamsPatch(troi.patch.Patch):
    """
        Taken a list of Recordings, break them into 7 roughly equal chunks and return
        the chunk for the given day of the week.
    """

    def __init__(self, debug=False):
        super().__init__(debug)
        self.recent_listens_lookup = None

    @staticmethod
    @cli.command(no_args_is_help=True)
    @click.argument('user_name')
    @click.argument('jam_date', required=False)
    def parse_args(**kwargs):
        """
        Generate a daily playlist from the ListenBrainz recommended recordings.

        \b
        USER_NAME is a MusicBrainz user name that has an account on ListenBrainz.
        JAM_DATE is the date for which the jam is created (this is needed to account for the fact different timezones
        can be on different dates). Recommended formatting for the date is 'YYYY-MM-DD DAY_OF_WEEK'.
        """

        return kwargs

    @staticmethod
    def outputs():
        return [Playlist]

    @staticmethod
    def slug():
        return "daily-jams"

    @staticmethod
    def description():
        return "Generate a daily playlist from the ListenBrainz recommended recordings."

    def apply_filters(self, user_name, element):
        deduped_raw_recs = troi.filters.DuplicateRecordingMBIDFilterElement()
        deduped_raw_recs.set_sources(element)

        raw_recs_lookup = troi.musicbrainz.recording_lookup.RecordingLookupElement()
        raw_recs_lookup.set_sources(deduped_raw_recs)

        # looking up recent listens is slow so reuse this element
        # (the element caches lookup internally so reusing it avoids slow api calls)
        if self.recent_listens_lookup is None:
            self.recent_listens_lookup = troi.listenbrainz.listens.RecentListensTimestampLookup(user_name, days=2)
        self.recent_listens_lookup.set_sources(raw_recs_lookup)

        latest_filter = troi.filters.LatestListenedAtFilterElement(DAYS_OF_RECENT_LISTENS_TO_EXCLUDE)
        latest_filter.set_sources(self.recent_listens_lookup)

        return latest_filter

    def check_and_add_more_recordings(self, user_name, recordings):
        # get the list of recordings we have so far, users who regularly listen to daily jams will have
        # most of their tracks get filtered out because the recs don't change a lot on daily basis. so
        # if there is a shortfall of tracks, move on the next 100 recommendations of the user. we could
        # fetch top 200 directly in first place but then there would be an equal chance of recommending
        # someone from their 101-200 range as from 1-100. we don't want that, we want to prefer the top
        # 100 over the next 100. so only ask for more recommendations if there is a shortfall.
        if len(recordings) < DAILY_JAMS_MIN_RECORDINGS:
            more_raw_recs = troi.listenbrainz.recs.UserRecordingRecommendationsElement(
                user_name=user_name,
                artist_type="raw",
                count=100,
                offset=100
            )
            further_recs = more_raw_recs.generate()
            recordings.extend(further_recs)

            all_recs = RecordingListElement(recordings)
            return self.apply_filters(user_name, all_recs)
        else:
            return RecordingListElement(recordings)

    def create(self, inputs):
        user_name = inputs['user_name']
        jam_date = inputs.get('jam_date')
        if jam_date is None:
            jam_date = datetime.utcnow().strftime("%Y-%m-%d %a")

        raw_recs = troi.listenbrainz.recs.UserRecordingRecommendationsElement(
            user_name=user_name,
            artist_type="raw",
            count=100
        )
        filtered_raw_recs = self.apply_filters(user_name, raw_recs)

        recordings = filtered_raw_recs.generate()
        recordings_element = self.check_and_add_more_recordings(user_name, recordings)

        pl_maker = PlaylistMakerElement(name="Daily Jams for %s, %s" % (user_name, jam_date),
                                        desc="Daily jams playlist!",
                                        patch_slug=self.slug())
        pl_maker.set_sources(recordings_element)

        reducer = PlaylistRedundancyReducerElement()
        reducer.set_sources(pl_maker)

        shuffle = PlaylistShuffleElement()
        shuffle.set_sources(reducer)

        return shuffle
