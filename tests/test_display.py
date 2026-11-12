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
