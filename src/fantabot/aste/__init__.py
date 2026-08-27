"""Harvesting live FantaLab auctions.

Pure logic and I/O are kept apart on purpose, and the split is enforced: the
default test tier opens no socket, so anything here that touches the network is
injected rather than imported.
"""
