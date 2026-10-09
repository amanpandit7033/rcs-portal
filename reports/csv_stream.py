"""Streaming CSV helper to export arbitrarily large querysets without memory exhaustion."""
import csv
from typing import Callable, Iterable, List
from django.http import StreamingHttpResponse


class Echo:
    """An object that implements just the write method of the file-like interface."""
    def write(self, value):
        return value


def stream_csv_response(
    filename: str,
    header: List[str],
    queryset_iterator: Iterable,
    row_formatter: Callable,
) -> StreamingHttpResponse:
    """Stream a CSV response efficiently using a generator."""
    pseudo_buffer = Echo()
    writer = csv.writer(pseudo_buffer)

    def row_generator():
        yield writer.writerow(header)
        for obj in queryset_iterator:
            yield writer.writerow(row_formatter(obj))

    response = StreamingHttpResponse(row_generator(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
