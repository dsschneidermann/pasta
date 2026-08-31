"""Unit tests for the readers-writer lock (src.rwlock).

Pure threading, no I/O: these pin the exclusion rules the store relies on.
"""

import threading

from src.rwlock import ReadWriteLock


def test_readers_run_concurrently():
    """Two readers must be able to hold the shared lock at the same time."""
    lock = ReadWriteLock()
    both_inside = threading.Barrier(2, timeout=5)

    def reader():
        with lock.read():
            both_inside.wait()   # deadlocks and raises BrokenBarrierError if reads exclude

    threads = [threading.Thread(target=reader) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_writer_excludes_readers():
    """A reader must not enter while a writer holds the exclusive lock."""
    lock = ReadWriteLock()
    writer_inside = threading.Event()
    reader_entered = threading.Event()
    release_writer = threading.Event()

    def writer():
        with lock.write():
            writer_inside.set()
            assert release_writer.wait(timeout=5)

    def reader():
        with lock.read():
            reader_entered.set()

    writer_thread = threading.Thread(target=writer)
    reader_thread = threading.Thread(target=reader)
    writer_thread.start()
    assert writer_inside.wait(timeout=5)
    reader_thread.start()
    assert not reader_entered.wait(timeout=0.2)   # blocked behind the writer
    release_writer.set()
    assert reader_entered.wait(timeout=5)         # and let through once it exits
    writer_thread.join(timeout=5)
    reader_thread.join(timeout=5)


def test_waiting_writer_blocks_new_readers():
    """Writer preference: while a writer waits, an arriving reader does not overtake it."""
    lock = ReadWriteLock()
    first_reader_inside = threading.Event()
    release_first_reader = threading.Event()
    writer_entered = threading.Event()
    late_reader_entered = threading.Event()

    def first_reader():
        with lock.read():
            first_reader_inside.set()
            assert release_first_reader.wait(timeout=5)

    def writer():
        with lock.write():
            writer_entered.set()

    def late_reader():
        with lock.read():
            late_reader_entered.set()

    threads = [threading.Thread(target=first_reader)]
    threads[0].start()
    assert first_reader_inside.wait(timeout=5)

    threads.append(threading.Thread(target=writer))
    threads[1].start()
    assert not writer_entered.wait(timeout=0.2)      # queued behind the live reader

    threads.append(threading.Thread(target=late_reader))
    threads[2].start()
    assert not late_reader_entered.wait(timeout=0.2)  # held out by the waiting writer

    release_first_reader.set()
    assert writer_entered.wait(timeout=5)            # writer goes first
    assert late_reader_entered.wait(timeout=5)       # then the late reader
    for thread in threads:
        thread.join(timeout=5)


def test_write_is_reentrant_for_its_owner():
    """A re-entered write lock is released once, at the outermost exit."""
    lock = ReadWriteLock()
    entered = threading.Event()

    def other_writer() -> None:
        with lock.write():
            entered.set()

    other = threading.Thread(target=other_writer)
    with lock.write():
        with lock.write():
            pass
        other.start()
        assert not entered.wait(timeout=0.2)   # the inner exit must not have released the lock
    assert entered.wait(timeout=5)             # the outermost exit does release it
    other.join(timeout=5)


def test_read_inside_write_does_not_deadlock():
    """A thread holding the write lock may enter read()."""
    lock = ReadWriteLock()
    with lock.write():
        with lock.read():
            pass
