"""Export Postman collection to JSON file."""
import json
import os
from django.conf import settings
from django.core.management.base import BaseCommand
from api.postman import get_postman_collection


class Command(BaseCommand):
    help = "Export Postman Collection v2.1 file for RCS Portal Public API"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default=os.path.join(settings.BASE_DIR, "rcs_portal_postman_collection.json"),
            help="Destination output path for JSON file",
        )

    def handle(self, *args, **options):
        output_path = options["output"]
        collection_data = get_postman_collection()

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(collection_data, f, indent=2)

        self.stdout.write(self.style.SUCCESS(f"Postman collection exported to {output_path}"))
