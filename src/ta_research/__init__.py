"""TA-1 research-only boundary. No upstream/broker imports or network on load."""
from .adapter import TAError, ReceiptStore, graph_config, make_receipt, run_with_graph, validate_request
