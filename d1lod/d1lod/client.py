from os import environ
from d1lod.stores.local_store import LocalStore
import RDF
import logging
import redis
from rq import Queue

from .filtered_d1_client import FilteredCoordinatingNodeClient
from .exceptions import UnsupportedFormatException
from .processors.eml.eml211_processor import EML211Processor
from .processors.eml.eml220_processor import EML220Processor
from .processors.iso.iso_processor import ISOProcessor
from .settings import ENVIRONMENTS, FILTERS

logger = logging.getLogger(__name__)


FORMAT_MAP = {
    "eml://ecoinformatics.org/eml-2.1.1": EML211Processor,
    "https://eml.ecoinformatics.org/eml-2.2.0": EML220Processor,
    "http://www.isotc211.org/2005/gmd": ISOProcessor,
}


class SlinkyClient:
    def __init__(
        self,
        environment=ENVIRONMENTS["development"],
        filter=FILTERS["sasap"],
    ):
        self.d1client = FilteredCoordinatingNodeClient(filter)
        self.store = environment["store"] or LocalStore
        self.redis = environment["redis"] or None

        if self.redis != None:
            self.queues = self.get_queues()

    def get_queues(self):
        return {
            "default": Queue("default", connection=self.redis),
            "dataset": Queue("dataset", connection=self.redis),
        }

    def process_dataset(self, identifier):
        model = self.get_model_for_dataset(identifier)
        self.store.insert_model(model)

    def get_model_for_dataset(self, identifier):
        logger.debug(f"SlinkyClient.get_model_for_dataset | {identifier}")

        storage = RDF.MemoryStorage()
        model = RDF.Model(storage)

        sysmeta = self.d1client.get_system_metadata(identifier)
        science_metadata = self.d1client.get_object(sysmeta)

        # Get Data Package parts (members)
        part_ids = self.d1client.get_parts(identifier)
        parts = [
            self.d1client.get_system_metadata(identifier) for identifier in part_ids
        ]

        # Process based on formatID
        if not sysmeta.formatId in FORMAT_MAP:
            raise UnsupportedFormatException(f"Unsupported format {sysmeta.formatId}")

        processor = FORMAT_MAP[sysmeta.formatId](
            self, model, sysmeta, science_metadata, parts
        )

        logging.info(
            "Getting model for dataset '{}' with processor '{}'".format(
                identifier, type(processor).__name__
            )
        )

        return processor.process()

    def get_new_datasets_since(self, datetime_str, batch_size=100):
        return self.d1client.query(
            {
                "q": f"dateModified:{{{datetime_str} TO NOW]",
                "sort": "dateModified asc",
                "rows": str(batch_size),
                "fl": "identifier,dateModified",
            }
        )
