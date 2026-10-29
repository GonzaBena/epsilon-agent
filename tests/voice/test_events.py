from epsilon.voice import events


def test_every_subscriber_receives_each_event_in_order():
    bus, first, second = events.Bus(), [], []
    bus.subscribe(first.append)
    bus.subscribe(second.append)

    bus.emit({"type": "status", "text": "hola"})
    bus.emit({"type": "reply", "text": "chau"})

    assert first == second == [{"type": "status", "text": "hola"}, {"type": "reply", "text": "chau"}]


def test_unsubscribing_stops_the_events_and_doing_it_twice_is_harmless():
    bus, seen = events.Bus(), []
    unsubscribe = bus.subscribe(seen.append)
    bus.emit({"type": "a"})

    unsubscribe()
    unsubscribe()
    bus.emit({"type": "b"})

    assert seen == [{"type": "a"}]


def test_a_failing_subscriber_never_stops_the_others_or_the_emitter():
    bus, seen = events.Bus(), []

    def broken(event):
        raise RuntimeError("boom")

    bus.subscribe(broken)
    bus.subscribe(seen.append)

    bus.emit({"type": "a"})

    assert seen == [{"type": "a"}]


def test_a_subscriber_can_unsubscribe_while_it_is_being_called():
    bus, seen = events.Bus(), []
    holder = {}

    def once(event):
        seen.append(event)
        holder["off"]()

    holder["off"] = bus.subscribe(once)
    bus.subscribe(seen.append)

    bus.emit({"type": "a"})
    bus.emit({"type": "b"})

    assert seen == [{"type": "a"}, {"type": "a"}, {"type": "b"}]


def test_the_event_a_subscriber_gets_is_a_copy():
    bus, seen = events.Bus(), []
    bus.subscribe(lambda event: event.update(text="cambiado"))
    bus.subscribe(seen.append)

    bus.emit({"type": "status", "text": "original"})

    assert seen == [{"type": "status", "text": "original"}]
