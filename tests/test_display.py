"""Display layer tests: LeelooDisplay rendering/caching and FrameAnimator.

No hardware needed. Fonts may be missing (PIL default font fallback).
"""
import asyncio
import os
import threading
import time

import numpy as np
import pytest
from PIL import Image

import gadget_display
from gadget_display import COLORS, GadgetDisplay, LeelooDisplay
from display.frame_animator import (
    EXPANDED_GEOMETRY,
    FRAME_GEOMETRIES,
    LEFT_PADDING,
    FrameAnimator,
    FrameType,
    get_frame_geometries,
    rgb_to_rgb565_fast,
    write_region_to_framebuffer_rowbyrow,
)

WEATHER = {'temp_f': 72, 'uv_raw': 5, 'rain_24h_inches': 0.5}
TIME = {'time_str': '1:00 PM', 'date_str': 'Nov 12', 'seconds': 30}
ALBUM = {'artist': 'Cinnamon Chasers', 'album': 'A Million Miles From Home',
         'track': 'Doorways', 'listeners': '262K', 'pushed_by': 'Amy'}
HANG = {'date_str': '1/02', 'time_str': '7:30pm', 'countdown_str': '2d:4h', 'slider_boxes': 6}
BG = (0x1A, 0x1D, 0x2E)


def make_art(path, size=(640, 800), color=(200, 30, 30)):
    Image.new('RGB', size, color).save(path)
    return str(path)


def bump_mtime(path):
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def check_frame(img):
    assert isinstance(img, Image.Image)
    assert img.size == (480, 320)
    assert img.mode == 'RGB'


@pytest.fixture
def display():
    return LeelooDisplay(preview_mode=True)


@pytest.fixture
def count_opens(monkeypatch):
    """Count Image.open calls per path."""
    calls = []
    real_open = Image.open

    def counting_open(fp, *a, **kw):
        calls.append(str(fp))
        return real_open(fp, *a, **kw)

    monkeypatch.setattr(Image, 'open', counting_open)
    return calls


# ---------------------------------------------------------------- render

def test_alias_and_colors():
    assert GadgetDisplay is LeelooDisplay
    for key in ('bg', 'green', 'purple', 'rose', 'tan', 'lavender', 'white'):
        assert key in COLORS


def test_render_normal(display, tmp_path):
    art = make_art(tmp_path / 'art.jpg')
    img = display.render(WEATHER, TIME, ['Amy', 'Ben'], ALBUM, album_art_path=art,
                         unread_counts={'Amy': 2}, hang=HANG)
    check_frame(img)
    assert display.image is img
    # the art (solid red) is on the right side
    r, g, b = img.getpixel((400, 160))
    assert r > 150 and g < 90 and b < 90


def test_render_all_empty(display):
    check_frame(display.render(None, TIME, [], None, hang=HANG))


def test_render_default_args_reads_hang(display):
    # hang=None -> format_countdown_display() (no hang file -> empty state)
    check_frame(display.render(WEATHER, TIME, ['Amy'], ALBUM))


def test_render_weather_error_differs(display):
    normal = display.render(WEATHER, TIME, [], None, hang=HANG).copy()
    err = display.render({'error': 'timeout'}, TIME, [], None, hang=HANG).copy()
    empty = display.render(None, TIME, [], None, hang=HANG).copy()
    check_frame(err)
    weather_box = (8, 18, 200, 74)
    assert err.crop(weather_box).tobytes() != normal.crop(weather_box).tobytes()
    assert err.crop(weather_box).tobytes() != empty.crop(weather_box).tobytes()
    # everything below the weather box is untouched
    below = (0, 80, 480, 320)
    assert err.crop(below).tobytes() == normal.crop(below).tobytes()


def test_weather_error_falsy_is_normal(display):
    a = display.render(dict(WEATHER, error=None), TIME, [], None, hang=HANG).copy()
    b = display.render(WEATHER, TIME, [], None, hang=HANG)
    assert a.tobytes() == b.tobytes()


def test_status_text_changes_header_only(display):
    base = display.render(WEATHER, TIME, ['Amy'], ALBUM, hang=HANG).copy()
    for status in ('listening...', 'thinking...', 'offline'):
        img = display.render(WEATHER, TIME, ['Amy'], ALBUM, hang=HANG, status_text=status)
        check_frame(img)
        assert img.crop((0, 0, 480, 12)).tobytes() != base.crop((0, 0, 480, 12)).tobytes()
        assert img.crop((0, 12, 480, 320)).tobytes() == base.crop((0, 12, 480, 320)).tobytes()
    # None / '' keep "tap to talk"
    assert display.render(WEATHER, TIME, ['Amy'], ALBUM, hang=HANG, status_text=None).tobytes() == base.tobytes()


def test_status_text_long_is_truncated_inside_frame(display):
    frame_x = display.album_frame_x(None)
    img = display.render(WEATHER, TIME, [], None, hang=HANG, status_text='x' * 200)
    check_frame(img)
    # nothing drawn in the header band between the info panel and the art frame,
    # and the art frame's own top edge is intact
    panel_right = frame_x - 5
    gap = img.crop((panel_right + 1, 0, frame_x, 3))
    assert gap.getcolors() == [(gap.width * gap.height, BG)]


def test_dict_contacts_match_string_contacts(display):
    a = display.render(WEATHER, TIME, ['Amy', 'Ben'], ALBUM, unread_counts={'Ben': 3}, hang=HANG).copy()
    b = display.render(WEATHER, TIME, [{'name': 'Amy'}, {'name': 'Ben', 'id': 7}], ALBUM,
                       unread_counts={'Ben': 3}, hang=HANG)
    check_frame(b)
    assert a.tobytes() == b.tobytes()
    # unread badge is looked up by name
    c = display.render(WEATHER, TIME, [{'name': 'Amy'}, {'name': 'Ben'}], ALBUM, hang=HANG)
    assert c.tobytes() != a.tobytes()


def test_odd_contacts_do_not_crash(display):
    check_frame(display.render(WEATHER, TIME, [{'id': 1}, {'name': None}, 'Zed'], ALBUM,
                               unread_counts={'Zed': 'x'}, hang=HANG))


def test_hang_argument_is_used_not_file(display, monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("format_countdown_display must not be called when hang= is given")

    monkeypatch.setattr(gadget_display, 'format_countdown_display', boom)
    a = display.render(WEATHER, TIME, [], None, hang=HANG).copy()
    other = dict(HANG, countdown_str='0h:05m', slider_boxes=0)
    b = display.render(WEATHER, TIME, [], None, hang=other)
    assert a.tobytes() != b.tobytes()
    with pytest.raises(AssertionError):
        display.render(WEATHER, TIME, [], None)


def test_render_swaps_in_complete_frame(display):
    """self.image is only ever replaced by a finished frame."""
    first = display.render(WEATHER, TIME, [], None, hang=HANG)
    seen = []
    real = display.draw_left_column

    def spy(*a, **kw):
        seen.append(display.image)
        return real(*a, **kw)

    display.draw_left_column = spy
    second = display.render(WEATHER, TIME, [], None, hang=HANG)
    assert seen == [first]          # mid-render, readers still see the old frame
    assert display.image is second and second is not first


def test_legacy_draw_on_self_image_still_works(display, tmp_path):
    """Other modules assign display.image/draw, then call the draw_* methods."""
    from PIL import ImageDraw
    art = make_art(tmp_path / 'art.png')
    display.image = Image.new('RGB', (480, 320), COLORS['bg'])
    display.draw = ImageDraw.Draw(display.image)
    x = display.draw_album_art(album_art_path=art)
    display.draw_left_column(WEATHER, TIME, ['Amy'], ALBUM, right_edge=x - 5)
    expected = display.render(WEATHER, TIME, ['Amy'], ALBUM, album_art_path=art)
    # same hang source (no file) -> identical frames
    assert display.image.tobytes() == expected.tobytes()


def test_message_and_reaction_views(display, tmp_path):
    art = make_art(tmp_path / 'art.png')
    check_frame(display.render_message_view('Amy', 'hello there ' * 10, album_art_path=art))
    gif = Image.new('RGBA', (64, 64), (255, 0, 0, 128))
    check_frame(display.render_reaction_view('Ben', gif, album_art_path=art))
    display.draw_reaction_overlay("<3\n<3", "Amy loved this")
    check_frame(display.image)
    assert display.overlay_active
    display.clear_overlay()
    assert not display.overlay_active


def test_show_preview_saves_without_gui(display, monkeypatch, tmp_path):
    saved = []
    monkeypatch.setattr(Image.Image, 'show', lambda self, *a, **kw: None)
    monkeypatch.setattr(Image.Image, 'save', lambda self, p, *a, **kw: saved.append(p))
    display.render(WEATHER, TIME, [], None, hang=HANG)
    display.show()
    assert saved


# ---------------------------------------------------------- album_frame_x

def test_album_frame_x_matches_draw_for_real_image(display, tmp_path):
    for i, size in enumerate([(640, 800), (500, 500), (243, 304), (300, 900)]):
        art = make_art(tmp_path / f'art{i}.png', size=size)
        fresh = LeelooDisplay()
        x = fresh.album_frame_x(art)            # before any draw
        assert isinstance(x, int)
        assert x == fresh.draw_album_art(art)
        assert x == fresh.album_frame_x(art)
    # 243x304 art -> 243 wide + 2*5 padding, 2px from the right edge
    assert display.album_frame_x(make_art(tmp_path / 'std.png', size=(243, 304))) == 480 - 253 - 2


def test_album_frame_x_missing_file_uses_placeholder_geometry(display, tmp_path):
    missing = str(tmp_path / 'nope.jpg')
    x = display.album_frame_x(missing)
    assert x == display.draw_album_art(missing)
    assert x == display.album_frame_x(None) == display.draw_album_art(None)
    assert x == 480 - 260 - 2


def test_album_frame_x_corrupt_file_uses_placeholder_geometry(display, tmp_path, count_opens):
    bad = tmp_path / 'bad.jpg'
    bad.write_bytes(b'this is not an image')
    x = display.album_frame_x(str(bad))
    assert x == display.draw_album_art(str(bad)) == display.album_frame_x(None)
    for _ in range(5):
        display.render(WEATHER, TIME, [], None, album_art_path=str(bad), hang=HANG)
    assert count_opens.count(str(bad)) == 1     # failure is remembered too


def test_album_frame_x_does_not_draw(display, tmp_path):
    art = make_art(tmp_path / 'art.png')
    before = display.image.tobytes()
    display.album_frame_x(art)
    display.album_frame_x(None)
    assert display.image.tobytes() == before


def test_gradient_fallback_when_no_placeholder(display, monkeypatch, tmp_path):
    real = LeelooDisplay._file_sig
    monkeypatch.setattr(LeelooDisplay, '_file_sig', staticmethod(lambda p: None))
    x = display.album_frame_x(None)
    img = display.render(None, TIME, [], None, hang=HANG)
    check_frame(img)
    assert x == 480 - 260 - 2
    assert img.getpixel((x + 100, 300)) != BG
    monkeypatch.setattr(LeelooDisplay, '_file_sig', staticmethod(real))


# ------------------------------------------------------------------ cache

def test_art_opened_once_across_many_renders(display, tmp_path, count_opens):
    art = make_art(tmp_path / 'art.jpg')
    frames = [display.render(WEATHER, TIME, ['Amy'], ALBUM, album_art_path=art, hang=HANG).tobytes()
              for _ in range(10)]
    display.album_frame_x(art)
    display.render_message_view('Amy', 'hi', album_art_path=art)
    assert count_opens.count(art) == 1
    assert len(set(frames)) == 1


def test_art_reopened_after_file_changes(display, tmp_path, count_opens):
    art = make_art(tmp_path / 'art.png', size=(640, 800), color=(200, 30, 30))
    display.render(WEATHER, TIME, [], ALBUM, album_art_path=art, hang=HANG)
    x1 = display.album_frame_x(art)
    assert count_opens.count(art) == 1

    # new content + new shape at the same path
    make_art(art, size=(500, 500), color=(30, 30, 200))
    bump_mtime(art)
    img = display.render(WEATHER, TIME, [], ALBUM, album_art_path=art, hang=HANG)
    assert count_opens.count(art) == 2
    x2 = display.album_frame_x(art)
    assert x2 != x1 and x2 == display.draw_album_art(art)
    r, g, b = img.getpixel((400, 160))
    assert b > 150 and r < 90

    # same size, only mtime changes -> still invalidates
    bump_mtime(art)
    display.render(WEATHER, TIME, [], ALBUM, album_art_path=art, hang=HANG)
    assert count_opens.count(art) == 3
    display.render(WEATHER, TIME, [], ALBUM, album_art_path=art, hang=HANG)
    assert count_opens.count(art) == 3


def test_path_change_and_cache_bound(display, tmp_path, count_opens):
    arts = [make_art(tmp_path / f'a{i}.png', color=(i * 20, 0, 0)) for i in range(8)]
    for art in arts:
        display.render(WEATHER, TIME, [], ALBUM, album_art_path=art, hang=HANG)
        display.render(WEATHER, TIME, [], ALBUM, album_art_path=art, hang=HANG)
    assert all(count_opens.count(a) == 1 for a in arts)
    assert len(display._art_cache) <= 4
    # deleted file falls back to placeholder geometry
    os.remove(arts[-1])
    assert display.album_frame_x(arts[-1]) == display.album_frame_x(None)


def test_placeholder_opened_once(display, count_opens):
    for _ in range(10):
        display.render(None, TIME, [], None, hang=HANG)
    placeholder_opens = [p for p in count_opens if p.endswith('.png')]
    assert len(placeholder_opens) <= 1


def test_leeloo_home_placeholder_is_used(monkeypatch, tmp_path, count_opens):
    home = tmp_path / 'home'
    home.mkdir()
    custom = home / 'leeloo_empty_scancode.png'
    Image.new('RGB', (800, 1000), (0, 255, 0)).save(custom)
    monkeypatch.setenv('LEELOO_HOME', str(home))
    d = LeelooDisplay()
    img = d.render(None, TIME, [], None, hang=HANG)
    assert str(custom) in count_opens
    r, g, b = img.getpixel((d.album_frame_x(None) + 130, 160))
    assert g > 200 and r < 50


def test_no_hardcoded_pi_path_in_source():
    src = open(gadget_display.__file__).read()
    assert src.count('/home/pi/leeloo-ui') == 1     # only the LEELOO_HOME default
    assert 'LEELOO_HOME' in src


def test_render_in_thread_while_album_frame_x_called(display, tmp_path):
    arts = [make_art(tmp_path / f't{i}.png', size=(400 + 40 * i, 800)) for i in range(3)]
    expected = {a: LeelooDisplay().album_frame_x(a) for a in arts}
    errors = []
    stop = threading.Event()

    def renderer():
        try:
            i = 0
            while not stop.is_set():
                img = display.render(WEATHER, TIME, ['Amy'], ALBUM, album_art_path=arts[i % 3], hang=HANG)
                assert img.size == (480, 320)
                i += 1
        except Exception as e:  # pragma: no cover
            errors.append(e)

    t = threading.Thread(target=renderer)
    t.start()
    try:
        deadline = time.time() + 0.5
        while time.time() < deadline:
            for a in arts:
                assert display.album_frame_x(a) == expected[a]
            check_frame(display.image)
    finally:
        stop.set()
        t.join()
    assert not errors


# --------------------------------------------------------- frame animator

FB_BYTES = 480 * 320 * 2


def test_public_names():
    assert LEFT_PADDING == 7
    assert set(FRAME_GEOMETRIES) == set(FrameType)
    assert EXPANDED_GEOMETRY.height == 290
    assert get_frame_geometries(200)[FrameType.WEATHER].right == 200


def test_rgb565_conversion():
    img = Image.new('RGB', (2, 1), (255, 255, 255))
    img.putpixel((1, 0), (255, 0, 0))
    arr = rgb_to_rgb565_fast(img)
    assert arr.shape == (1, 2) and arr.dtype == np.uint16
    assert arr[0, 0] == 0xFFFF and arr[0, 1] == 0xF800


def test_write_region_rowbyrow(tmp_path):
    fb = tmp_path / 'fb'
    fb.write_bytes(b'\x00' * FB_BYTES)
    region = np.full((3, 4), 0xABCD, dtype=np.uint16)
    write_region_to_framebuffer_rowbyrow(region, 10, 20, str(fb))
    data = np.frombuffer(fb.read_bytes(), dtype=np.uint16).reshape(320, 480)
    assert (data[20:23, 10:14] == 0xABCD).all()
    assert np.count_nonzero(data) == 12


def test_async_expand_collapse_preview(display):
    animator = FrameAnimator(display, box_right=208, fb_path=None)
    done = []
    drawn = []

    def content(image, geom, rx, ry):
        drawn.append((image.size, threading.current_thread() is threading.main_thread()))

    async def run():
        await animator.async_expand(FrameType.WEATHER, content_drawer=content,
                                    on_complete=lambda: done.append('expand'))
        await animator.async_collapse(FrameType.WEATHER, on_complete=lambda: done.append('collapse'))

    asyncio.run(run())
    assert done == ['expand', 'collapse']
    assert drawn == [((201, 296), False)]       # drawn once (last frame), off the event loop thread


def test_async_animation_yields_every_frame_even_when_late(display, monkeypatch):
    """A slow clock (always late) must still yield once per frame."""
    animator = FrameAnimator(display, box_right=208, fb_path=None)
    import display.frame_animator as fa

    clock = [0.0]

    def fake_time():
        clock[0] += 10.0        # every frame is hopelessly late
        return clock[0]

    monkeypatch.setattr(fa.time, 'time', fake_time)
    ticks = []

    async def ticker():
        while True:
            ticks.append(1)
            await asyncio.sleep(0)

    async def run():
        task = asyncio.create_task(ticker())
        await animator.async_expand(FrameType.TIME)
        task.cancel()

    asyncio.run(run())
    assert len(ticks) >= FrameAnimator.FRAME_COUNT


def test_async_animation_writes_only_inside_region(display, tmp_path, monkeypatch):
    fb = tmp_path / 'fb1'
    fb.write_bytes(b'\x00' * FB_BYTES)
    box_right = 208
    animator = FrameAnimator(display, box_right=box_right, fb_path=str(fb))

    import builtins
    import display.frame_animator as fa
    opens = []
    real_open = builtins.open

    def counting_open(path, *a, **kw):
        if str(path) == str(fb):
            opens.append(path)
        return real_open(path, *a, **kw)

    monkeypatch.setattr(builtins, 'open', counting_open)

    async def run():
        await animator.async_expand(FrameType.MESSAGES)

    asyncio.run(run())
    monkeypatch.undo()

    assert len(opens) == 1                      # opened once per animation
    assert fb.stat().st_size == FB_BYTES        # never grows
    data = np.frombuffer(fb.read_bytes(), dtype=np.uint16).reshape(320, 480)
    x0, y0, w, h = 7, 10, box_right - 7, 296
    inside = data[y0:y0 + h, x0:x0 + w]
    assert np.count_nonzero(inside) == inside.size      # whole region written (bg is non-zero)
    outside = data.copy()
    outside[y0:y0 + h, x0:x0 + w] = 0
    assert np.count_nonzero(outside) == 0


def test_sync_animation_with_fake_framebuffer(display, tmp_path, monkeypatch, capsys):
    fb = tmp_path / 'fb1'
    fb.write_bytes(b'\x00' * FB_BYTES)
    animator = FrameAnimator(display, box_right=208, fb_path=str(fb))
    import display.frame_animator as fa
    monkeypatch.setattr(fa.time, 'sleep', lambda s: None)
    done = []
    animator.collapse(FrameType.ALBUM, on_complete=lambda: done.append(1))
    assert done == [1]
    data = np.frombuffer(fb.read_bytes(), dtype=np.uint16).reshape(320, 480)
    assert np.count_nonzero(data[10:306, 7:208]) == 296 * 201
    assert np.count_nonzero(data[:10]) == 0 and np.count_nonzero(data[306:]) == 0
    assert np.count_nonzero(data[:, :7]) == 0 and np.count_nonzero(data[:, 208:]) == 0


# ------------------------------------------------------------------ packs
# (docs/PACKS.md: theme colours, header, labels, custom panel in the weather box)

from gadget_display import DEFAULT_COLORS

PANEL_TEXT = {'title': 'live', 'rows': [
    {'left': 'UNC', 'right': '78', 'bar': None},
    {'left': 'DUKE', 'right': '70', 'bar': None},
    {'left': 'period 2', 'right': '4:12', 'bar': None},
]}
PANEL_BAR = {'title': None, 'rows': [
    {'left': 'score', 'right': None, 'bar': 0.5},
    {'left': 'full', 'right': None, 'bar': 1.0},
    {'left': 'none', 'right': None, 'bar': 0.0},
]}
PANEL_LONG = {'title': 'W' * 40, 'rows': [
    {'left': 'W' * 28, 'right': 'W' * 14, 'bar': None},
    {'left': 'W' * 200, 'right': 'W' * 200, 'bar': None},
    {'left': 'W' * 200, 'right': None, 'bar': 0.75},
]}
THEME = {'bg': '#13294B', 'tan': '#7BAFD4', 'lavender': '#FFFFFF', 'purple': '#B9D9EB', 'green': '#7BAFD4'}


@pytest.fixture
def default_theme():
    """The palette is module-wide: always give it back."""
    yield
    COLORS.clear()
    COLORS.update(DEFAULT_COLORS)


def weather_box(display):
    """(left, top, right, bottom) of the weather box with its label, for crop()."""
    box_right = display.album_frame_x(None) - 5 - 5
    return (7, 10, box_right + 1, 76)


def outside_equal(a, b, box):
    """True if two frames are the same everywhere outside `box`."""
    x = np.asarray(a).copy()
    y = np.asarray(b).copy()
    left, top, right, bottom = box
    x[top:bottom, left:right] = 0
    y[top:bottom, left:right] = 0
    return np.array_equal(x, y)


def render_base(display, **kw):
    return display.render(WEATHER, TIME, ['Amy', 'Ben'], ALBUM, hang=HANG, **kw).copy()


@pytest.mark.parametrize('panel', [PANEL_TEXT, PANEL_BAR, PANEL_LONG], ids=['text', 'bar', 'long'])
def test_custom_panel_draws_only_inside_weather_box(display, panel):
    base = render_base(display)
    img = render_base(display, custom_panel=panel)
    check_frame(img)
    box = weather_box(display)
    assert img.crop(box).tobytes() != base.crop(box).tobytes()
    assert outside_equal(img, base, box)
    # ... and the frame of the box itself is not drawn over (2px tan border)
    tan = tuple(int(COLORS['tan'][i:i + 2], 16) for i in (1, 3, 5))
    for edge in ((box[2] - 2, 30, box[2], 74), (7, 30, 9, 74), (7, 74, box[2], 76)):
        strip = img.crop(edge)
        assert strip.getcolors() == [(strip.width * strip.height, tan)], edge


def test_custom_panel_inner_text_stays_inside_border(display):
    """Long text is cut: the padding column next to the right border stays empty."""
    img = render_base(display, custom_panel=PANEL_LONG)
    box = weather_box(display)
    pad = img.crop((box[2] - 6, 24, box[2] - 2, 73))       # between text and border
    assert pad.getcolors() == [(pad.width * pad.height, BG)]
    left_pad = img.crop((9, 24, 12, 73))
    assert left_pad.getcolors() == [(left_pad.width * left_pad.height, BG)]


def test_custom_panel_replaces_weather_content(display):
    """weather_data is not drawn: any weather gives the same frame."""
    a = render_base(display, custom_panel=PANEL_TEXT)
    b = display.render({'temp_f': 5, 'uv_raw': 11, 'rain_24h_inches': 3}, TIME, ['Amy', 'Ben'], ALBUM,
                       hang=HANG, custom_panel=PANEL_TEXT)
    c = display.render(None, TIME, ['Amy', 'Ben'], ALBUM, hang=HANG, custom_panel=PANEL_TEXT)
    d = display.render({'error': 'timeout'}, TIME, ['Amy', 'Ben'], ALBUM, hang=HANG, custom_panel=PANEL_TEXT)
    assert a.tobytes() == b.tobytes() == c.tobytes() == d.tobytes()


def test_custom_panel_rows_and_bar_change_the_picture(display):
    box = weather_box(display)

    def crop(panel):
        return display.render(WEATHER, TIME, [], None, hang=HANG, custom_panel=panel).crop(box).tobytes()

    def one(**row):
        return {'title': None, 'rows': [dict({'left': 'x', 'right': None, 'bar': None}, **row)]}

    assert crop(one(right='78')) != crop(one(right='80'))
    assert crop(one(left='UNC')) != crop(one(left='DUKE'))
    assert crop(one(bar=0.0)) != crop(one(bar=0.5)) != crop(one(bar=1.0))
    assert crop(one(bar=0.0)) != crop(one())
    # bar is clamped to 0..1, 12 boxes
    assert crop(one(bar=1.0)) == crop(one(bar=7)) and crop(one(bar=0.0)) == crop(one(bar=-1))
    assert crop(one(bar=0.5)) == crop(one(bar=6 / 12))
    # at most 3 rows are drawn
    rows = [{'left': f'row {i}', 'right': None, 'bar': None} for i in range(5)]
    assert crop({'title': None, 'rows': rows}) == crop({'title': None, 'rows': rows[:3]})
    # the title is the label of the box; no title -> the pack label, else "weather"
    assert crop({'title': 'live', 'rows': rows[:1]}) != crop({'title': None, 'rows': rows[:1]})
    with_label = display.render(WEATHER, TIME, [], None, hang=HANG, labels={'weather': 'live'},
                                custom_panel={'title': None, 'rows': rows[:1]})
    assert with_label.crop(box).tobytes() == crop({'title': 'live', 'rows': rows[:1]})


def test_custom_panel_odd_data_does_not_crash(display):
    base = render_base(display)
    for panel in ({}, {'rows': None}, {'title': 5, 'rows': [None, 'x', {}]},
                  {'rows': [{'left': None, 'right': 7, 'bar': 'x'}]},
                  {'rows': [{'left': 'a\nb\tc', 'right': '', 'bar': float('nan')}]}):
        img = render_base(display, custom_panel=panel)
        check_frame(img)
        assert outside_equal(img, base, weather_box(display))
    # not a dict -> the normal weather
    assert render_base(display, custom_panel='live').tobytes() == base.tobytes()
    assert render_base(display, custom_panel=None, header=None, labels=None).tobytes() == base.tobytes()
    assert render_base(display, header='', labels={}).tobytes() == base.tobytes()


def test_header_replaces_leeloo_text(display):
    base = render_base(display)
    img = render_base(display, header='GO HEELS')
    check_frame(img)
    name = (6, 0, 89, 12)                       # "LEELOO v1.0" is at x=8..89
    assert img.crop(name).tobytes() != base.crop(name).tobytes()
    assert outside_equal(img, base, name)       # "tap to talk" and the rest are the same
    # a long header is cut and does not reach "tap to talk"
    long = render_base(display, header='W' * 60)
    assert long.crop(name).tobytes() != base.crop(name).tobytes()
    assert outside_equal(long, base, name)
    # works together with a status text
    a = render_base(display, header='GO HEELS', status_text='listening...')
    b = render_base(display, status_text='listening...')
    assert a.crop(name).tobytes() == img.crop(name).tobytes()
    assert outside_equal(a, b, name)


@pytest.mark.parametrize('name,y', [('weather', 16), ('time', 83), ('messages', 162), ('album', 198)])
def test_labels_replace_box_labels(display, name, y):
    base = render_base(display)
    img = render_base(display, labels={name: 'crew'})
    check_frame(img)
    region = (9, y - 6, weather_box(display)[2] - 2, y + 10)     # label text with descenders
    assert img.crop(region).tobytes() != base.crop(region).tobytes()
    assert outside_equal(img, base, region)
    # unknown names are ignored; a long label stays on the top edge of its box
    assert render_base(display, labels={'other': 'x'}).tobytes() == base.tobytes()
    long = render_base(display, labels={name: 'W' * 80})
    assert outside_equal(long, base, region)


def test_apply_theme_changes_and_restores(display, default_theme, tmp_path):
    colors_obj = gadget_display.COLORS
    art = make_art(tmp_path / 'art.png')
    args = (WEATHER, TIME, ['Amy'], ALBUM)
    before = display.render(*args, album_art_path=art, unread_counts={'Amy': 2}, hang=HANG).copy()
    assert before.getpixel((300, 2)) == BG

    assert display.apply_theme(THEME) is True
    themed = display.render(*args, album_art_path=art, unread_counts={'Amy': 2}, hang=HANG).copy()
    assert themed.getpixel((300, 2)) == (0x13, 0x29, 0x4B)
    assert themed.getpixel((7, 40)) == (0x7B, 0xAF, 0xD4)          # weather box border (tan)
    assert themed.getpixel((2, 100)) == (255, 255, 255)            # outer frame (lavender)
    assert BG not in [c for _, c in themed.crop((0, 0, 200, 320)).getcolors(100000)]

    # same object, default copy untouched
    assert gadget_display.COLORS is colors_obj is COLORS
    assert COLORS['bg'] == '#13294B' and COLORS['rose'] == DEFAULT_COLORS['rose']
    assert DEFAULT_COLORS['bg'] == '#1A1D2E' and DEFAULT_COLORS is not COLORS

    for restore in (None, {}):
        display.apply_theme(THEME)
        assert display.apply_theme(restore) is True
        assert COLORS == DEFAULT_COLORS
        after = display.render(*args, album_art_path=art, unread_counts={'Amy': 2}, hang=HANG)
        assert after.tobytes() == before.tobytes()


def test_apply_theme_is_on_top_of_default_not_previous_theme(display, default_theme):
    display.apply_theme({'bg': '#000000', 'tan': '#111111'})
    display.apply_theme({'tan': '#222222'})
    assert COLORS['bg'] == DEFAULT_COLORS['bg'] and COLORS['tan'] == '#222222'
    # unknown names / bad values are ignored, never an error
    assert display.apply_theme({'tan': '#222222', 'pink': '#FF00FF', 'bg': 'navy', 'rose': None, 5: 5}) is False
    assert set(COLORS) == set(DEFAULT_COLORS)
    assert display.apply_theme('blue') is True and COLORS == DEFAULT_COLORS


def test_apply_theme_same_colours_again_is_cheap(display, default_theme):
    assert display.apply_theme(THEME) is True
    display.render(None, TIME, [], None, hang=HANG)
    dim_cache = dict(display._dim_cache)
    art_keys = list(display._art_cache)
    assert dim_cache
    for _ in range(3):
        assert display.apply_theme(dict(THEME)) is False        # nothing changed
    assert display._dim_cache == dim_cache and list(display._art_cache) == art_keys
    t0 = time.perf_counter()
    for _ in range(2000):
        display.apply_theme(THEME)
    assert time.perf_counter() - t0 < 1.0


def test_dim_colour_follows_the_theme_background(display, default_theme):
    assert display._dim_color('#FFFFFF') == '#5e606c'            # 30% white on navy
    empty = display.render(None, TIME, [], None, hang=HANG).copy()
    display.apply_theme({'bg': '#000000'})
    assert display._dim_color('#FFFFFF') == '#4c4c4c'            # 30% white on black
    # a second display sees the new palette too (COLORS is shared), no stale cache
    other = LeelooDisplay()
    assert other._dim_color('#FFFFFF') == '#4c4c4c'
    other.apply_theme(None)
    assert display._dim_color('#FFFFFF') == '#5e606c'
    assert display.render(None, TIME, [], None, hang=HANG).tobytes() == empty.tobytes()


def test_gradient_placeholder_follows_theme(display, default_theme, monkeypatch):
    monkeypatch.setattr(LeelooDisplay, '_file_sig', staticmethod(lambda p: None))
    before = display.render(None, TIME, [], None, hang=HANG).copy()
    display.apply_theme({'bg': '#402000'})
    themed = display.render(None, TIME, [], None, hang=HANG)
    assert themed.getpixel((300, 2)) == (0x40, 0x20, 0x00)
    assert BG not in [c for _, c in themed.getcolors(200000)]   # no navy left anywhere
    display.apply_theme(None)
    assert display.render(None, TIME, [], None, hang=HANG).tobytes() == before.tobytes()


def test_views_and_animator_follow_theme(display, default_theme, tmp_path):
    display.apply_theme(THEME)
    bg = (0x13, 0x29, 0x4B)
    assert display.render_message_view('Amy', 'hello').getpixel((100, 150)) == bg
    gif = Image.new('RGBA', (64, 64), (0, 0, 0, 0))               # fully transparent
    assert display.render_reaction_view('Ben', gif).getpixel((100, 150)) == bg

    # an animator made BEFORE the theme change uses the new colours
    display.apply_theme(None)
    fb = tmp_path / 'fb1'
    fb.write_bytes(b'\x00' * FB_BYTES)
    animator = FrameAnimator(display, box_right=208, fb_path=str(fb))
    display.apply_theme(THEME)

    async def run():
        await animator.async_expand(FrameType.WEATHER)

    asyncio.run(run())
    data = np.frombuffer(fb.read_bytes(), dtype=np.uint16).reshape(320, 480)

    def rgb565(rgb):
        r, g, b = rgb
        return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)

    assert data[150, 100] == rgb565(bg)                          # interior
    assert data[150, 7] == rgb565((0x7B, 0xAF, 0xD4))            # border = themed tan
    assert rgb565(BG) not in np.unique(data[10:306, 7:208])


def test_render_with_pack_manager_output(display, default_theme, tmp_path):
    """The real path: PackManager -> apply_theme + render."""
    import json
    from leeloo_packs import PackManager
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, 'packs', 'unc-hoops.json'), encoding='utf-8') as f:
        pack = json.load(f)
    packs = PackManager(str(tmp_path / 'pack.json'))
    assert packs.install(pack) == []
    packs.set_values('unc-hoops', {'game.state': 'in', 'game.home': 'UNC', 'game.home_score': '78',
                                   'game.away': 'DUKE', 'game.away_score': '70',
                                   'game.period': '2', 'game.clock': '4:12'})
    display.apply_theme(packs.theme_colors())

    def frame():
        return display.render(WEATHER, TIME, ['Amy'], ALBUM, hang=HANG, header=packs.header(),
                              labels=packs.labels(), custom_panel=packs.current_panel()).copy()

    live = frame()
    check_frame(live)
    assert live.getpixel((300, 2)) == (0x13, 0x29, 0x4B)
    sig = packs.signature()
    packs.set_values('unc-hoops', dict(packs.values, **{'game.home_score': '80'}))
    assert packs.signature() != sig
    scored = frame()
    box = weather_box(display)
    assert scored.crop(box).tobytes() != live.crop(box).tobytes()
    assert outside_equal(scored, live, box)
