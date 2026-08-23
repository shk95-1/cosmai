import socket

import pytest


def test_an_unmarked_test_cannot_open_a_socket():
    with pytest.raises(RuntimeError, match="offline by construction"):
        socket.create_connection(("example.com", 80))


def test_the_guard_names_the_test_that_tripped_it():
    with pytest.raises(RuntimeError, match="test_the_guard_names_the_test_that_tripped_it"):
        socket.socket().connect(("example.com", 80))
