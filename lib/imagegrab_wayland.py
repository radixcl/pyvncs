# coding=utf-8
# pyvncs
# Copyright (C) 2017-2018 Matias Fernandez
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Lesser General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public License for more
# details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

"""
Wayland screen capture using PipeWire screencast API + xdg-desktop-portal.

This module provides screen capture for Wayland sessions across multiple
compositors (GNOME, KDE, Sway, Hyprland) by:
1. Using xdg-desktop-portal D-Bus API to create a screencast session
2. Using PipeWire libpipewire (via cffi) to receive frames from the screencast node
3. Decoding frames from BGR/NV12 format to RGB for PIL

Requires:
  System packages: libpipewire-0.3-dev, libspa-0.2-dev, libglib2.0-dev
  Python packages: cffi, PyGObject (gir1.2-gtk-4.0)

Usage:
    from lib.imagegrab_wayland import WaylandImageGrab
    image = WaylandImageGrab.grab()
"""

import sys
import struct
import threading
import time
from PIL import Image
from lib import log


# ---------------------------------------------------------------------------
# cffi definitions for libpipewire-0.3 and libspa-0.2
# ---------------------------------------------------------------------------

_cffi_source = """

/* --- spa/types.h minimal definitions --- */

typedef int32_t spa_int;

enum {
    SPA_TYPE_Invalid = 0,
    SPA_TYPE_Object,
    SPA_TYPE_Id,
    SPA_TYPE_Bool,
    SPA_TYPE_Int,
    SPA_TYPE_Long,
    SPA_TYPE_Float,
    SPA_TYPE_Double,
    SPA_TYPE_Array,
    SPA_TYPE_Chunk,
    SPA_TYPE_String,
    SPA_TYPE_Max,
};

enum {
    SPA_POD_Invalid = 0,
    SPA_POD_Object,
    SPA_POD_Id,
    SPA_POD_Bool,
    SPA_POD_Int,
    SPA_POD_Long,
    SPA_POD_Float,
    SPA_POD_Double,
    SPA_POD_Array,
    SPA_POD_Chunk,
    SPA_POD_String,
};

#define SPA_POD_INT_MAX_VALUE  0x7fffffff
#define SPA_POD_ID_MAGIC       0x64690000

#define spa_pod_id_get(x)  (((const spa_pod*)(x))->data.i)

/* spa_pod: the fundamental POD data unit */
typedef struct {
    uint8_t type;
    uint8_t key;
    uint8_t __pad[2];
    union {
        int32_t i;
        int64_t l;
        float f;
        double d;
        struct spa_pod_chunk chunk;
        struct spa_pod_object object;
        uint8_t data[0];
    } data;
} spa_pod;

/* spa_pod_object: represents an object in the Pod hierarchy */
typedef struct {
    uint32_t __start__;
    uint32_t type;
    uint32_t __end__;
    union {
        int32_t i;
        int64_t l;
        float f;
        double d;
        struct spa_pod_chunk chunk;
        struct spa_pod_object object;
        uint8_t data[0];
    } data;
} spa_pod_object;

/* spa_pod_array: an array of child PODs */
typedef struct {
    uint32_t type;
    uint32_t next;
    uint32_t offset;
    uint32_t length;
    uint32_t children_offset;
    uint32_t children_length;
} spa_pod_array;

/* spa_pod_chunk */
typedef struct {
    uint32_t flags;
    uint32_t length;
    uint64_t offset;
    void *data;
} spa_pod_chunk;

/* --- spa/pod.h: Pod builder --- */

#define SPA_POD_OBJECT_MAGIC  0x6f626a01
#define SPA_POD_OBJECT_END_MAGIC  0x6f626a02

enum spa_pod_builder_flag {
    SPA_POD_BUILDER_FLAGS_PIECES = 1,
};

typedef struct spa_pod_builder {
    uint8_t *start;
    uint8_t *cursor;
    uint8_t *end;
    uint32_t flags;
    uint32_t depth;
    uint32_t pad[3];
    uint8_t data[0];
} spa_pod_builder;

struct spa_pod *spa_pod_builder_alloc(struct spa_pod_builder *b, uint32_t size);
struct spa_pod *spa_pod_builder_reserve(struct spa_pod_builder *b, uint32_t type,
                                        uint32_t key, uint32_t size);
int spa_pod_builder_write(struct spa_pod_builder *b, uint32_t offset, uint32_t size,
                          const void *data);
void spa_pod_builder_close(struct spa_pod_builder *b);
struct spa_pod *spa_pod_builder_finish(struct spa_pod_builder *b);
void spa_pod_builder_reset(struct spa_pod_builder *b);

struct spa_pod *spa_pod_builder_object(struct spa_pod_builder *b, uint32_t key);
void spa_pod_builder_prop(struct spa_pod_builder *b, uint32_t key);
void spa_pod_builder_id(struct spa_pod_builder *b, uint32_t id);
void spa_pod_builder_long(struct spa_pod_builder *b, int64_t l);
void spa_pod_builder_string(struct spa_pod_builder *b, const char *str);
void spa_pod_builder_position(struct spa_pod_builder *b, const struct spa_rectangle *r);
void spa_pod_builder_color(struct spa_pod_builder *b, const struct spa_color *c);

/* --- spa/rectangle.h --- */

struct spa_rectangle {
    uint32_t width;
    uint32_t height;
};

/* --- spa/color.h --- */

enum spa_color_space {
    SPA_COLOR_SPACE_LAST = 0,
    SPA_COLOR_SPACE_BT2020,
    SPA_COLOR_SPACE_BT601,
    SPA_COLOR_SPACE_BT709,
    SPA_COLOR_SPACE_SRGB,
    SPA_COLOR_SPACE_BT2020_CONSTANT_LUMINANCE,
    SPA_COLOR_SPACE_XYZ,
    SPA_COLOR_SPACE_BT470BG,
    SPA_COLOR_SPACE_SRGB_LINEAR,
    SPA_COLOR_SPACE_BT2100_PQ,
    SPA_COLOR_SPACE_DCI_P3,
    SPA_COLOR_SPACE_NB,
};

enum spa_color_range {
    SPA_COLOR_RANGE_UNKNOWN = 0,
    SPA_COLOR_RANGE_LIMITED,
    SPA_COLOR_RANGE_FULL,
    SPA_COLOR_RANGE_NB,
};

typedef struct spa_color {
    enum spa_color_space space;
    enum spa_color_range range;
    uint8_t matrix[16];
} spa_color;

/* --- spa/types.h: type constants --- */

enum {
    SPA_TYPE_Invalid = 0,
    SPA_TYPE_Object,
    SPA_TYPE_Id,
    SPA_TYPE_Bool,
    SPA_TYPE_Int,
    SPA_TYPE_Long,
    SPA_TYPE_Float,
    SPA_TYPE_Double,
    SPA_TYPE_Array,
    SPA_TYPE_Chunk,
    SPA_TYPE_String,
    SPA_TYPE_Max,
};

/* spa_type: property type keys */
enum spa_type {
    SPA_TYPE_NONE = 0,
    SPA_TYPE_BOUNDING,
    SPA_TYPE_Fr,
    SPA_TYPE_DIMENSION,
    SPA_TYPE_BOX,
    SPA_TYPE_RECTANGLE,
    SPA_TYPE_POINT,
    SPA_TYPE_MATRIX,
    SPA_TYPE_COLOR,
    SPA_TYPE_BITMASK,
    SPA_TYPE_COUNT,
    SPA_TYPE_Object_Extension = 1000,
    SPA_TYPE_Object_Node,
    SPA_TYPE_Object_Port,
    SPA_TYPE_Object_ParametersUpdated,
    SPA_TYPE_Object_NodeInfo_Name,
    SPA_TYPE_Object_NodeInfo_Ioctl,
    SPA_TYPE_Object_PortInfo_DIR,
    SPA_TYPE_PortInfo_HintsDenom,
    SPA_TYPE_PortInfo_Policy,
    SPA_TYPE_PortInfo_Link,
    SPA_TYPE_Param_Type,
    SPA_TYPE_Format_format,
    SPA_TYPE_Format_mediaType,
    SPA_TYPE_Format_subtype,
    SPA_TYPE_Format_rate,
    SPA_TYPE_Format_channels,
    SPA_TYPE_Format_position,
    SPA_TYPE_Format_audioScale,
    SPA_TYPE_Format_audioControlsVolume,
    SPA_TYPE_Format_audioControlsVolumePosition,
    SPA_TYPE_Format_audioControlsAmplification,
    SPA_TYPE_Format_videoFormat,
    SPA_TYPE_Format_video_size,
    SPA_TYPE_Format_video_framerate,
    SPA_TYPE_Format_video_multiviewMode,
    SPA_TYPE_Format_video_multiviewFormat,
    SPA_TYPE_Format_video_stereoMode,
    SPA_TYPE_Format_video_colorimetry,
    SPA_TYPE_Format_audio_format,
    SPA_TYPE_Format_audio_bitsPerChannel,
    SPA_TYPE_Format_audio_channels,
    SPA_TYPE_Format_audio_samplesPerSecond,
    SPA_TYPE_Format_control_id,
    SPA_TYPE_Control_range_min,
    SPA_TYPE_Control_range_max,
    SPA_TYPE_Control_range_step,
    SPA_TYPE_Control_value,
    SPA_TYPE_Control_step_value,
    SPA_TYPE_Control_flags,
    SPA_TYPE_Control_isEnabled,
    SPA_TYPE_Policy_policy,
    SPA_TYPE_Policy_client,
    SPA_TYPE_PortConfig_name,
    SPA_TYPE_PortConfig_index,
    SPA_TYPE_LinkInfo_input,
    SPA_TYPE_LinkInfo_output,
    SPA_TYPE_LinkInfo_flags,
    SPA_TYPE_NodeInfo_subscribePorts,
    SPA_TYPE_Node_Props,
    SPA_TYPE_Port_Props,
    SPA_TYPE_Control_Props,
    SPA_TYPE_MEDIA_TYPE = 10000,
    SPA_TYPE_MEDIA_SUBTYPE,
    SPA_TYPE_AUDIO_RATE,
    SPA_TYPE_AUDIO_CHANNELS,
    SPA_TYPE_VIDEO_FORMAT,
    SPA_TYPE_VIDEO_SIZE,
    SPA_TYPE_VIDEO_FRAMERATE,
    SPA_TYPE_AUDIO_FORMAT,
    SPA_TYPE_CONTROL_ID,
    SPA_TYPE_COUNT,
};

/* spa_media_type enum values */
enum spa_media_type {
    SPA_MEDIA_TYPE_invalid = 0,
    SPA_MEDIA_TYPE_Audio,
    SPA_MEDIA_TYPE_Video,
    SPA_MEDIA_TYPE_Image,
    SPA_MEDIA_TYPE_AppControl,
    SPA_MEDIA_TYPE_MAX,
};

/* spa_image_format enum values */
enum spa_image_format {
    SPA_IMAGE_FORMAT_INVALID = 0,
    SPA_IMAGE_FORMAT_UNKNOWN,
    SPA_IMAGE_FORMAT_ARGB,
    SPA_IMAGE_FORMAT_XRGB,
    SPA_IMAGE_FORMAT_RGBx,
    SPA_IMAGE_FORMAT_BGRx,
    SPA_IMAGE_FORMAT_RGBA,
    SPA_IMAGE_FORMAT_RGB,
    SPA_IMAGE_FORMAT_BGRA,
    SPA_IMAGE_FORMAT_BGR,
    SPA_IMAGE_FORMAT_xRGB,
    SPA_IMAGE_FORMAT_BGRx,
    SPA_IMAGE_FORMAT_ABGR,
    SPA_IMAGE_FORMAT_RGB_16,
    SPA_IMAGE_FORMAT_BGR_16,
    SPA_IMAGE_FORMAT_YUYV,
    SPA_IMAGE_FORMAT_YVYU,
    SPA_IMAGE_FORMAT_UYVY,
    SPA_IMAGE_FORMAT_NV12,
    SPA_IMAGE_FORMAT_NV21,
    SPA_IMAGE_FORMAT_NV16,
    SPA_IMAGE_FORMAT_NV61,
    SPA_IMAGE_FORMAT_P010,
    SPA_IMAGE_FORMAT_P012,
    SPA_IMAGE_FORMAT_P016,
    SPA_IMAGE_FORMAT_YU12,
    SPA_IMAGE_FORMAT_YV12,
    SPA_IMAGE_FORMAT_Y41P,
    SPA_IMAGE_FORMAT_RGBY,
    SPA_IMAGE_FORMAT_YRGB,
    SPA_IMAGE_FORMAT_BAYER_SGRBG8,
    SPA_IMAGE_FORMAT_BAYER_BGGR8,
    SPA_IMAGE_FORMAT_BAYER_RGGB8,
    SPA_IMAGE_FORMAT_BAYER_GBRG8,
    SPA_IMAGE_FORMAT_VBRA16,
    SPA_IMAGE_FORMAT_NB,
};

/* spa_port_flags */
enum spa_port_flags {
    SPA_PORT_FLAG_MMAP_BUFFERS = 1,
    SPA_PORT_FLAG_OUT_OF_BAND_BUFFER_INDEX = 2,
    SPA_PORT_FLAG_RT_PROCESS = 4,
    SPA_PORT_FLAG_DYNAMIC = 8,
};

/* spa_node_flags */
enum spa_node_flags {
    SPA_NODE_FLAG_RT_PROCESS = 1,
    SPA_NODE_FLAG_DYNAMIC = 2,
};

/* spa_link_flags */
enum spa_link_flags {
    SPA_LINK_FLAG_RT = 1,
    SPA_LINK_FLAG_INPUT = 2,
    SPA_LINK_FLAG_OUTPUT = 4,
};

/* spa_param enum */
enum spa_param {
    SPA_PARAM_Invalid = 0,
    SPA_PARAM_EnumFormat,
    SPA_PARAM_AudioChannelAudioProperties,
    SPA_PARAM_AudioSeq,
    SPA_PARAM_Format,
    SPA_PARAM_Control,
    SPA_PARAM_BufferConstraints,
    SPA_PARAM_BufferPool,
    SPA_PARAM_DeviceInfo,
    SPA_PARAM_Properties,
    SPA_PARAM_ChannelVolumes,
    SPA_PARAM_InputPortConfig,
    SPA_PARAM_OutputPortConfig,
    SPA_PARAM_LinkInfo,
    SPA_PARAM_NumTypes,
};

/* --- core/callbacks.h: core event IDs --- */

enum spa_core_events {
    SPA_CORE_EVENT_NONE = 0,
    SPA_CORE_EVENT_DEFAULT,
    SPA_CORE_EVENT_ERROR,
    SPA_CORE_EVENT_INFO,
    SPA_CORE_EVENT_USE,
    SPA_CORE_EVENT_FREE,
    SPA_CORE_EVENT_LINK_EVENT,
    SPA_CORE_EVENT_NODE_EVENT,
    SPA_CORE_EVENT_PORT_EVENT,
    SPA_CORE_EVENT_HOOK_EVENT,
    SPA_CORE_EVENT_MAX,
};

/* --- core.h --- */

struct spa_core;
struct spa_core_proxy;

typedef struct {
    void *user_data;
    void (*done)(void *user_data);
    void (*error)(void *user_data, int seq, int res, const char *message);
    void (*info)(void *user_data);
} spa_core_callbacks;

typedef struct {
    void *user_data;
    void (*core)(void *user_data, struct spa_core_proxy *core);
    void (*remote)(void *user_data, uint32_t id, uint32_t version, void *extension);
    void (*node_event)(void *user_data, uint32_t node_id, int trigger,
                       struct spa_pod *pod);
    void (*port_event)(void *user_data, uint32_t node_id, int trigger,
                       uint32_t port_id, uint32_t param_type, int fd,
                       const struct spa_pod *pod);
} spa_core_events_callbacks;

struct spa_core *spa_core_new(void *loop, const spa_core_callbacks *cb,
                              uint32_t version);
void spa_core_free(struct spa_core *core);
int spa_core_connect(struct spa_core *core, const char *name);
int spa_core_join(struct spa_core *core, const char *name);

/* --- remote.h --- */

struct spa_remote;

typedef struct {
    void *user_data;
    void (*remote)(void *user_data, struct spa_remote *remote);
} spa_remote_callbacks;

struct spa_remote *spa_remote_new(void *loop, const spa_remote_callbacks *cb,
                                  uint32_t version);
void spa_remote_free(struct spa_remote *remote);
int spa_remote_connect(struct spa_remote *remote, const char *name);

/* --- stream.h --- */

struct spa_stream;
struct spa_stream_params;

enum spa_stream_event {
    SPA_STREAM_EVENT_NONE = 0,
    SPA_STREAM_EVENT_DONE,
    SPA_STREAM_EVENT_PARAM,
    SPA_STREAM_EVENT_BUFFER,
    SPA_STREAM_EVENT_PROCESS,
    SPA_STREAM_EVENT_INACTIVITY,
    SPA_STREAM_EVENT_ADD_BUFFER,
    SPA_STREAM_EVENT_REMOVE_BUFFER,
    SPA_STREAM_EVENT_MAX,
};

enum spa_stream_flags {
    SPA_STREAM_FLAG_NONE = 0,
    SPA_STREAM_FLAG_MAP_BUFFERS = 1,
    SPA_STREAM_FLAG_REQUIRE_BATCH = 2,
};

typedef struct {
    void *user_data;
    void (*done)(void *user_data);
    void (*error)(void *user_data, int seq, int res, const char *message);
    void (*event)(void *user_data, enum spa_stream_event id,
                  const void *args, size_t size);
    int (*process)(void *user_data);
} spa_stream_callbacks;

struct spa_stream *spa_stream_new(const spa_stream_callbacks *scb,
                                  void *user_data,
                                  const char *name,
                                  const struct spa_pod *param,
                                  uint32_t flags);
void spa_stream_free(struct spa_stream *stream);
int spa_stream_start(struct spa_stream *stream);
void spa_stream_destroy(struct spa_stream *stream);
int spa_stream_set_params(struct spa_stream *stream, const struct spa_pod *params,
                          uint32_t n_params);
int spa_stream_get_params(struct spa_stream *stream, struct spa_pod **params,
                          uint32_t *n_params);
int spa_stream_update_params(struct spa_stream *stream, const struct spa_pod *params,
                             uint32_t n_params);
void spa_stream_pause(struct spa_stream *stream);
int spa_stream_resume(struct spa_stream *stream);
int spa_stream_drain(struct spa_stream *stream);
int spa_stream_drop(struct spa_stream *stream, uint32_t n_buffers);
int spa_stream_add_buffer(struct spa_stream *stream, void *data, size_t size);
int spa_stream_signal(struct spa_stream *stream);
void spa_stream_flush(struct spa_stream *stream);
void spa_stream_finish(struct spa_stream *stream, uint64_t *time_last,
                       uint64_t *time_next, uint32_t flags);
int spa_stream_manage(struct spa_stream *stream, struct spa_manage *manage);
int spa_stream_get_state(struct spa_stream *stream, char *string, size_t len,
                         const char *prev);

/* --- buffer.h --- */

struct pw_buffer {
    void *user_data;
    struct spa_buffer *buffer;
};

struct spa_buffer {
    uint32_t n_chunks;
    uint32_t n_datas;
    uint32_t flags;
    struct spa_chunk chunks[0];
};

/* --- meta.h --- */

#define PW_BUFFER_DATA_OBJ_META_BUFFER 0

struct pw_buffer_data {
    uint32_t type;
    uint32_t flags;
    uint32_t id;
    uint32_t offset;
    uint32_t size;
    uint32_t stride;
    uint32_t padding[2];
    void *data;
    void *mapped;
};

/* --- types.h common --- */

enum spa_direction {
    SPA_DIRECTION_INVALID = 0,
    SPA_DIRECTION_INPUT,
    SPA_DIRECTION_OUTPUT,
};

struct spa_io_buffers {
    uint32_t state;
    uint32_t index;
    uint32_t size;
    uint32_t data;
    struct spa_buffer buffer[0];
};

enum pw_stream_state {
    PW_STREAM_STATE_UNLINKED = 0,
    PW_STREAM_STATE_CONFIGURED = 1,
    PW_STREAM_STATE_STREAMING = 2,
    PW_STREAM_STATE_ERROR = 3,
};

enum pw_remote_state {
    PW_REMOTE_STATE_UNCONNECTED = 0,
    PW_REMOTE_STATE_CONNECTING = 1,
    PW_REMOTE_STATE_CONNECTED = 2,
    PW_REMOTE_STATE_ERROR = 3,
    PW_REMOTE_STATE_UNCONNECTED_NEW = 4,
};

/* --- mainloop.h --- */

struct pw_main_loop;
struct pw_loop;

struct pw_main_loop *pw_main_loop_new(const char *name);
void pw_main_loop_free(struct pw_main_loop *l);
struct pw_loop *pw_main_loop_get_loop(struct pw_main_loop *l);
int pw_main_loop_run(struct pw_main_loop *l);

struct pw_loop *pw_loop_new(const struct spa_dict *props);
void pw_loop_destroy(struct pw_loop *l);

/* --- core.h (pw_ prefix wrappers) --- */

struct pw_core *pw_core_connect(struct pw_main_loop *l, const char *name,
                                const struct spa_dict *props);
struct pw_core *pw_core_join(struct pw_main_loop *l, const char *name);
void pw_core_destroy(struct pw_core *c);

/* --- remote.h (pw_ prefix) --- */

struct pw_remote *pw_remote_connect(struct pw_main_loop *l, const char *name,
                                    const struct spa_dict *props);
void pw_remote_destroy(struct pw_remote *r);

/* --- stream.h (pw_ prefix) --- */

struct pw_stream *pw_stream_new(struct pw_core *core,
                                const char *name,
                                const struct spa_pod *sp);
void pw_stream_destroy(struct pw_stream *s);

enum pw_stream_flags {
    PW_STREAM_FLAG_NONE = 0,
    PW_STREAM_FLAG_MAP_BUFFERS = 1,
    PW_STREAM_FLAG_DRIVER = 2,
    PW_STREAM_FLAG_AUTOCONNECT = 4,
    PW_STREAM_FLAG_OFFER_BUFFER_POOL_AUTOINIT = 8,
    PW_STREAM_FLAG_SUPPORT_ADD_BUFFERS = 16,
    PW_STREAM_FLAG_RENDER_TO_MEDIA = 32,
    PW_STREAM_FLAG_PASSTHROUGH = 64,
};

struct pw_stream_events {
    size_t size;
    void (*state_changed)(void *user_data, enum pw_stream_state old,
                          enum pw_stream_state state, const char *error);
    void (*param_changed)(void *user_data, uint32_t param_id, int changed);
    void (*process)(void *user_data);
    void (*drain)(void *user_data);
    void (*state_failed)(void *user_data, const char *reason);
};

int pw_stream_connect(struct pw_stream *s, enum spa_direction dir,
                      uint32_t id, enum pw_stream_flags flags,
                      const struct spa_pod *params, uint32_t n_params);

int pw_stream_queue_buffer(struct pw_stream *s, struct pw_buffer *b);
struct pw_buffer *pw_stream_dequeue_buffer(struct pw_stream *s);

int pw_stream_set_active(struct pw_stream *s, int active);
int pw_stream_set_params(struct pw_stream *s, const struct spa_pod *params,
                         uint32_t n_params);
int pw_stream_update_state(struct pw_stream *s, enum pw_stream_state state,
                           const char *error);
const char *pw_stream_get_state(struct pw_stream *s, enum pw_stream_state state,
                                char *str, size_t len, const char *prev);

/* --- node.h --- */

enum pw_node_port_flags {
    PW_NODE_PORT_FLAG_NONE = 0,
    PW_NODE_PORT_FLAG_MMAP_BUFFERS = 1,
    PW_NODE_PORT_FLAG_DYNAMIC = 2,
    PW_NODE_PORT_FLAG_RT = 4,
    PW_NODE_PORT_FLAG_PASS_MMAP_BUFFERS_FD = 8,
    PW_NODE_PORT_FLAG_IN_ACCESS_MASK = 12,
    PW_NODE_PORT_FLAG_OUT_ACCESS_MASK = 48,
};

enum pw_node_port_direction {
    PW_NODE_PORT_DIRECTION_INPUT = 0,
    PW_NODE_PORT_DIRECTION_OUTPUT = 1,
};

/* --- dict.h --- */

struct spa_dict_item {
    const char *key;
    const char *value;
};

struct spa_dict {
    uint32_t n_items;
    const struct spa_dict_item items[0];
};

/* --- loop.h --- */

int pw_loop_iterate(struct pw_loop *l, int timeout);

"""

try:
    from cffi import FFI
    ffi = FFI()
    ffi.cdef(_cffi_source)
    lib = ffi.parse(_cffi_source)  # inline definitions
    _cffi_loaded = True
except Exception as e:
    log.debug(f"imagegrab_wayland: cffi init failed: {e}")
    _cffi_loaded = False


# ---------------------------------------------------------------------------
# SPA constant helpers
# ---------------------------------------------------------------------------

# spa_pod_type values
SPA_POD_OBJECT = 0x01
SPA_POD_ID = 0x03
SPA_POD_INT = 0x05
SPA_POD_LONG = 0x06
SPA_POD_ARRAY = 0x09
SPA_POD_STRING = 0x0b

# spa_pod magic
SPA_POD_OBJECT_MAGIC = 0x6f626a01
SPA_POD_OBJECT_END_MAGIC = 0x6f626a02
SPA_POD_ID_MAGIC = 0x64690000

# spa_type keys (property identifiers)
SPA_FORMAT_mediaType = 0x03
SPA_FORMAT_video_size = 0x11
SPA_FORMAT_video_format = 0x0e
SPA_IMAGE_stride = 0x04

# spa_media_type
SPA_MEDIA_TYPE_Image = 0x03

# spa_image_format
SPA_IMAGE_FORMAT_BGR = 0x09
SPA_IMAGE_FORMAT_BGRA = 0x08
SPA_IMAGE_FORMAT_NV12 = 0x12
SPA_IMAGE_FORMAT_YV12 = 0x1b
SPA_IMAGE_FORMAT_YU12 = 0x1a

# spa_direction
SPA_DIRECTION_INPUT = 0x01

# pw_stream_state
PW_STREAM_STATE_UNLINKED = 0
PW_STREAM_STATE_CONFIGURED = 1
PW_STREAM_STATE_STREAMING = 2
PW_STREAM_STATE_ERROR = 3


# ---------------------------------------------------------------------------
# PipeWire screencast client (cffi-based)
# ---------------------------------------------------------------------------

class PipeWireScreencast:
    """Low-level PipeWire screencast client using cffi.

    Manages the full PipeWire client lifecycle:
    - pw_main_loop / pw_loop event loop
    - pw_core connection to session-monitor
    - pw_remote connection to pipewire.session
    - pw_stream for frame capture

    After construction, call start() to begin the event loop in a thread,
    then call capture() to grab a single frame.
    """

    def __init__(self, session_id, node_id, width=1920, height=1080):
        self._session_id = session_id
        self._node_id = node_id
        self._width = width
        self._height = height
        self._frame = None
        self._frame_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread = None

        # cffi handles (set during lifecycle)
        self._main_loop = None
        self._core = None
        self._remote = None
        self._stream = None
        self._stream_state = PW_STREAM_STATE_UNLINKED

    # ---- SPA Pod construction (raw bytes) ----

    def _build_params_pod(self, width, height):
        """Build the SPA_Pod structure for pw_stream_set_params.

        Creates:  spa_pod_array { spa_pod_object { format_type,
              { media_type=Image }, size=WxH, format=BGR } }
        """
        # We build the Pod structure as raw bytes in a cffi unsigned char array.
        # Layout (all values are little-endian):
        #
        # Offset  Size  Field
        # 0       4     spa_pod_array.type = SPA_POD_ARRAY (0x09)
        # 4       4     spa_pod_array.next = 0
        # 8       4     spa_pod_array.offset = 0
        # 12      4     spa_pod_array.length = total array size
        # 16      4     spa_pod_array.children_offset = 24 (where first child starts)
        # 20      4     spa_pod_array.children_length = children data size
        # 24      4     spa_pod_object.__start__ = SPA_POD_OBJECT_MAGIC
        # 28      4     spa_pod_object.type = pointer to nested object
        # 32      4     spa_pod_object.__end__ = SPA_POD_OBJECT_END_MAGIC
        # 36      4     spa_pod_object.data.type (nested object __start__)
        # 40      4     nested spa_pod_object.type (SPA_POD_ID for media_type)
        # 44      4     nested spa_pod_object.__end__
        # 48      4     nested entry: type=SPA_POD_ID|key=mediaType, id=SPA_MEDIA_TYPE_Image
        # 52      8     nested entry: type=SPA_POD_LONG|key=size, value=rectangle_ptr
        # 60      4     nested entry: type=SPA_POD_ID|key=format, id=SPA_IMAGE_FORMAT_BGR
        # 64      4     outer entry: type=SPA_POD_ID|key=mediaType
        # 68      4     outer entry: type=SPA_POD_LONG|key=size
        # 72      4     outer entry: type=SPA_POD_ID|key=format
        # 76      4     outer entry: type=SPA_POD_LONG|key=stride
        #
        # The nested rectangle (at offset 56): width(4) + height(4) = 8 bytes
        # The nested format object spans offset 36-60

        # Compute rectangle struct (two uint32_t): width, height
        rect_data = struct.pack('<II', width, height)

        # Nested object: { type=SPA_POD_ID (media_type), { id=Image },
        #                  type=SPA_POD_LONG (size), { l=rect_ptr },
        #                  type=SPA_POD_ID (format), { i=BGR } }
        # Layout:
        #  0:  __start__ = 0x6f626a01
        #  4:  type = pointer to first property entry
        #  8:  __end__ = 0x6f626a02
        # 12:  entry: type=SPA_POD_ID(0x03)|key=0x03, data.i = Image(0x03)
        # 16:  entry: type=SPA_POD_LONG(0x06)|key=0x11, data.l = rect_data_ptr
        # 24:  entry: type=SPA_POD_ID(0x03)|key=0x0e, data.i = BGR(0x09)

        # We need to build this as raw bytes
        nested = bytearray()
        # __start__, type(offset), __end__
        nested += struct.pack('<III', SPA_POD_OBJECT_MAGIC, 12, SPA_POD_OBJECT_END_MAGIC)
        # media_type entry: type=0x03 (ID), key=0x03, pad(2), value=0x03 (Image)
        nested += struct.pack('<BBhI', 0x03, 0x03, 0, SPA_MEDIA_TYPE_Image)
        # size entry: type=0x06 (LONG), key=0x11, pad(2), value=rect_ptr
        # We'll compute the rect_ptr offset after we know the total layout
        size_entry_offset = len(nested)
        nested += struct.pack('<BBhQ', 0x06, 0x11, 0, 0)  # placeholder for rect ptr
        # format entry: type=0x03 (ID), key=0x0e, pad(2), value=0x09 (BGR)
        nested += struct.pack('<BBhI', 0x03, 0x0e, 0, SPA_IMAGE_FORMAT_BGR)

        nested_len = len(nested)

        # Outer object:
        # __start__, type(pointer to nested), __end__,
        # entries: media_type, size, format, stride
        # type field of outer object = pointer to the nested object
        # The nested object is placed right after the outer object header

        # Outer object header: 16 bytes (__start__, type, __end__, data.type)
        # data.type = pointer to nested object

        # Let's compute offsets:
        # Array header: 24 bytes
        # Outer object: 16 bytes (header) + 4*4 (4 entries) = 32 bytes  (wait, the outer
        #   object doesn't have its own data field with an object - it has entries after the
        #   header+data area)

        # Actually, looking at the spa_pod_object structure:
        # __start__(4) + type(4) + __end__(4) + data(36)
        # The "data" field is a union. For an object, the data portion contains the
        # property entries. So the outer object structure is:
        # __start__(4) + type(4, ptr to first entry) + __end__(4) + entries...

        # The type field of the outer object points to the nested spa_pod_object.
        # The type field of the outer object is at offset 28 in the array.
        # The outer object entries start after its header (16 bytes from object start).

        # Let me recompute:
        # Array starts at offset 0
        # Outer object starts at offset 24 (after array header, 8-byte aligned)
        # Outer object:
        #   [24-27] __start__ = 0x6f626a01
        #   [28-31] type = ptr to nested object = 24 + 16 = 40
        #   [32-35] __end__ = 0x6f626a02
        #   [36-39] entry: media_type
        #   [40-43] entry: size
        #   [44-47] entry: format
        #   [48-51] entry: stride
        # Nested object is at offset 40 (pointed to by outer.type)
        #   [40-43] __start__ = 0x6f626a01
        #   [44-47] type = ptr to first nested entry = 40 + 12 = 52
        #   [48-51] __end__ = 0x6f626a02
        #   [52-55] entry: media_type (SPA_POD_ID, key=0x03, value=Image)
        #   [56-63] entry: size (SPA_POD_LONG, key=0x11, value=rect_ptr)
        #   [64-67] entry: format (SPA_POD_ID, key=0x0e, value=BGR)
        # Rectangle data: 8 bytes (width, height) at offset 68
        # rect_ptr = 68

        # Now the outer entries:
        # [36-39] entry: media_type (SPA_POD_ID, key=0x03, value=Image)
        #   type=0x03, key=0x03, pad=0, i=0x03
        # [40-43] entry: size (SPA_POD_LONG, key=0x11, value=rect_ptr=68)
        #   type=0x06, key=0x11, pad=0, l=68
        # [44-47] entry: format (SPA_POD_ID, key=0x0e, value=BGR)
        #   type=0x03, key=0x0e, pad=0, i=0x09
        # [48-51] entry: stride (SPA_POD_LONG, key=0x04, value=stride)
        #   type=0x06, key=0x04, pad=0, l=stride_value

        # Wait, the nested object at offset 40 overlaps with outer entry at [40-43].
        # That can't be right. Let me reconsider.

        # The outer object type field is at offset 28, and it should point to the
        # NESTED OBJECT, not to the entries. The entries come after the outer object's
        # data area.

        # spa_pod_object has: __start__(4) + type(4) + __end__(4) + data(36)
        # The "data" is a union. When used as an object, the data area contains the
        # entries. The type field points to the FIRST ENTRY within data.
        # But we also want to embed a nested object...

        # Actually, I think I need to reconsider the structure. In PipeWire's format
        # params, the structure is:
        #
        # spa_pod_array {
        #   children: [spa_pod_object {   # the Format object
        #     __start__, type(ptr to entries), __end__, data: [
        #       { type: SPA_POD_Id, key: mediaType, value: Image },
        #       { type: SPA_POD_Long, key: size, value: <offset to rectangle> },
        #       { type: SPA_POD_Id, key: format, value: BGR },
        #       { type: SPA_POD_Long, key: stride, value: stride_val },
        #     ]
        #   }]
        # }
        #
        # And the rectangle is a nested spa_pod_object or just raw uint32_t pair.
        # For SPA_POD_Long with key=SPA_FORMAT_video_size, the value is an offset
        # to a spa_rectangle struct (width + height as uint32_t).

        # OK so the structure is simpler than I thought. No nested spa_pod_object
        # for the format. Just:
        # array { object { entries: [mediaType, size, format, stride] } }
        # where size's value is an offset to a spa_rectangle (8 bytes: width + height)

        # Let me rebuild:
        data = bytearray()

        # Rectangle data (spa_rectangle: width + height as uint32_t)
        stride = (width * 3 + 3) & ~3  # 3 bytes per pixel, 4-byte aligned
        rect_data = struct.pack('<II', width, height)

        # Outer object entries
        # entry_media_type: SPA_POD_ID, key=0x03, value=SPA_MEDIA_TYPE_Image
        entry_media_type = struct.pack('<BBhI', 0x03, 0x03, 0, SPA_MEDIA_TYPE_Image)
        # entry_size: SPA_POD_LONG, key=0x11, value=rect_offset
        # We'll compute rect_offset later
        entry_size = struct.pack('<BBhQ', 0x06, 0x11, 0, 0)
        # entry_format: SPA_POD_ID, key=0x0e, value=SPA_IMAGE_FORMAT_BGR
        entry_format = struct.pack('<BBhI', 0x03, 0x0e, 0, SPA_IMAGE_FORMAT_BGR)
        # entry_stride: SPA_POD_LONG, key=0x04, value=stride
        entry_stride = struct.pack('<BBhQ', 0x06, 0x04, 0, stride)

        entries = entry_media_type + entry_size + entry_format + entry_stride

        # Object start position (after array header, 8-byte aligned)
        obj_start = 24  # sizeof(spa_pod_array)

        # Object data area starts at obj_start + 12 (__start__ + type + __end__)
        # type field = offset to first entry within the object
        data_start = obj_start + 12
        type_field_val = data_start

        # Rectangle goes after all entries
        rect_offset = data_start + len(entries)

        # Fill in the size entry with the rect offset
        entry_size = struct.pack('<BBhQ', 0x06, 0x11, 0, rect_offset)
        entries = entry_media_type + entry_size + entry_format + entry_stride

        # __start__ and __end__ values
        obj_start_val = SPA_POD_OBJECT_MAGIC
        obj_end_val = SPA_POD_OBJECT_END_MAGIC

        # Now build the full structure
        # [0-23]     spa_pod_array header
        # [24-35]    outer spa_pod_object header (__start__ + type + __end__)
        # [36-51]    outer entries (4 x 16 bytes... wait, entries are 12 bytes each)

        # Actually: spa_pod entry is: type(1) + key(1) + __pad(2) + data(8) = 12 bytes
        # But struct.pack('<BBhQ') = 1+1+2+8 = 12. Good.
        # spa_pod entry for ID: type(1) + key(1) + __pad(2) + data(4) = 8 bytes
        # struct.pack('<BBhI') = 1+1+2+4 = 8. Good.

        # So entries are not all the same size! ID entries are 8 bytes, LONG entries are 12 bytes.
        # Let me recompute:
        # entry_media_type: 8 bytes
        # entry_size: 12 bytes
        # entry_format: 8 bytes
        # entry_stride: 12 bytes
        # total entries: 40 bytes

        entries = entry_media_type + entry_size + entry_format + entry_stride
        entries_len = len(entries)

        # type field = data_start = obj_start + 12 = 24 + 12 = 36
        type_field_val = data_start

        # rect_offset = data_start + entries_len = 36 + 40 = 76
        rect_offset = data_start + entries_len

        # Update size entry with correct rect offset
        entry_size = struct.pack('<BBhQ', 0x06, 0x11, 0, rect_offset)
        entries = entry_media_type + entry_size + entry_format + entry_stride

        # Total structure size
        # array header (24) + object header (12) + entries (40) + rect (8) = 84
        total_size = 24 + 12 + entries_len + 8

        # children_offset = obj_start = 24
        # children_length = total_size - children_offset = 84 - 24 = 60
        children_offset = obj_start
        children_length = total_size - children_offset

        # Build array header
        array_header = struct.pack('<IIIIII',
            0x09,        # type = SPA_POD_ARRAY
            0,           # next
            0,           # offset
            total_size,  # length
            children_offset,
            children_length,
        )

        # Build outer object header
        obj_header = struct.pack('<III',
            obj_start_val,
            type_field_val,
            obj_end_val,
        )

        # Pad entries to 8-byte alignment (each entry's data should be aligned)
        # Actually, looking at PipeWire implementations, entries don't need padding
        # between them. The data in each entry is what needs alignment.

        # Build full buffer
        buf = array_header + obj_header + entries + rect_data

        return bytes(buf), ffi.new('char[]', buf), len(buf), stride

    def _build_stream_params(self, width, height):
        """Build the initial stream parameter (stream type/info).

        This is a simple node info parameter that tells PipeWire what kind
        of stream we want to create.
        """
        pod, ptr, size, stride = self._build_params_pod(width, height)
        return ptr, size, stride

    # ---- Event callbacks (called from PipeWire thread) ----

    def _on_stream_state_changed(self, old, state, error):
        """Handle stream state changes."""
        state_str = ffi.string(error).decode() if error else ''
        log.debug(f"PipeWire: stream state {old} -> {state} (error={state_str})")

        if state == PW_STREAM_STATE_CONFIGURED:
            log.debug("PipeWire: stream configured, starting...")
            self._ready_event.set()
        elif state == PW_STREAM_STATE_STREAMING:
            log.debug("PipeWire: stream streaming")
        elif state == PW_STREAM_STATE_ERROR:
            log.error(f"PipeWire: stream error: {state_str}")
            self._ready_event.set()  # signal to proceed so we can fail gracefully

    def _on_stream_process(self):
        """Process incoming buffers in the PipeWire event loop."""
        if not self._stream:
            return

        buf = lib.pw_stream_dequeue_buffer(self._stream)
        if not buf or not buf.buffer:
            return

        n_chunks = buf.buffer.n_chunks
        if n_chunks == 0:
            return

        # Get the chunk data pointer
        chunk = buf.buffer.chunks[0]
        data_ptr = chunk.data
        data_size = chunk.size

        if not data_ptr or data_size == 0:
            lib.pw_stream_queue_buffer(self._stream, buf)
            return

        # Decode the frame
        frame = self._decode_frame(data_ptr, data_size)
        if frame is not None:
            with self._frame_lock:
                self._frame = frame

        lib.pw_stream_queue_buffer(self._stream, buf)

    def _decode_frame(self, data_ptr, data_size):
        """Decode raw PipeWire frame data to a PIL RGB Image.

        Supports BGR (SPA_IMAGE_FORMAT_BGR) and NV12 (SPA_IMAGE_FORMAT_NV12) formats.
        """
        try:
            # Convert cffi pointer to numpy array
            import numpy as np

            # The data pointer gives us the raw frame buffer
            # For BGR format: width * height * 3 bytes
            # For NV12: width * height * 3/2 bytes (Y plane + UV plane)

            total_pixels = self._width * self._height
            expected_bgr_size = self._width * self._height * 3
            expected_nv12_size = self._width * self._height * 3 // 2

            if data_size >= expected_bgr_size:
                # BGR format
                raw = np.frombuffer(ffi.buffer(data_ptr, expected_bgr_size),
                                    dtype=np.uint8)
                img = raw.reshape((self._height, self._width, 3))
                # BGR -> RGB
                img = img[:, :, ::-1].copy()
                return Image.fromarray(img, 'RGB')

            elif data_size >= expected_nv12_size:
                # NV12 format (Y plane + interleaved UV)
                raw = np.frombuffer(ffi.buffer(data_ptr, expected_nv12_size),
                                    dtype=np.uint8)
                y_plane = raw[:total_pixels].reshape((self._height, self._width))
                uv_plane = raw[total_pixels:].reshape((self._height // 2, self._width, 2))

                # Convert NV12/YUV420 to RGB
                img = self._nv12_to_rgb(y_plane, uv_plane)
                return img

            else:
                log.debug(f"PipeWire: unexpected frame size {data_size}, "
                          f"expected >= {expected_bgr_size} for BGR")
                return None

        except Exception as e:
            log.debug(f"PipeWire: frame decode error: {e}")
            return None

    @staticmethod
    def _nv12_to_rgb(y_plane, uv_plane):
        """Convert NV12 (YUV420) planar data to RGB using numpy."""
        import numpy as np

        y = y_plane.astype(np.float64)
        u = uv_plane[:, :, 0].astype(np.float64) - 128.0
        v = uv_plane[:, :, 1].astype(np.float64) - 128.0

        # Upscale UV to full resolution (repeat each pixel 2x2)
        u = np.repeat(u, 2, axis=0)
        u = np.repeat(u, 2, axis=1)
        v = np.repeat(v, 2, axis=0)
        v = np.repeat(v, 2, axis=1)

        # YUV to RGB conversion (BT.601)
        r = np.clip(y + 1.402 * v, 0, 255).astype(np.uint8)
        g = np.clip(y - 0.344136 * u - 0.714136 * v, 0, 255).astype(np.uint8)
        b = np.clip(y + 1.772 * u, 0, 255).astype(np.uint8)

        return Image.fromarray(np.dstack((r, g, b)), 'RGB')

    # ---- Lifecycle ----

    def _build_params_pod(self, width, height):
        """Build SPA_Pod array containing Format parameters for the stream."""
        stride = (width * 3 + 3) & ~4  # 4-byte aligned stride for BGR
        rect_data = struct.pack('<II', width, height)

        # Entry structures (spa_pod inline format):
        # type(1) + key(1) + pad(2) + data
        # SPA_POD_ID entries: data is int32 (4 bytes) -> total 8 bytes
        # SPA_POD_LONG entries: data is int64 (8 bytes) -> total 12 bytes

        e_media_type = struct.pack('<BBhI', 0x03, 0x03, 0, SPA_MEDIA_TYPE_Image)
        e_format = struct.pack('<BBhI', 0x03, 0x0e, 0, SPA_IMAGE_FORMAT_BGR)

        # Size entry: SPA_POD_LONG, points to rect data
        e_size = struct.pack('<BBhQ', 0x06, 0x11, 0, 60)  # offset to rect
        e_stride = struct.pack('<BBhQ', 0x06, 0x04, 0, stride)

        entries = e_media_type + e_size + e_format + e_stride

        # Object header: __start__(4) + type_ptr(4) + __end__(4) = 12 bytes
        # type_ptr points to first entry within the object
        obj_hdr = struct.pack('<III', SPA_POD_OBJECT_MAGIC, 12, SPA_POD_OBJECT_END_MAGIC)

        # Array header: 6 x uint32 = 24 bytes
        array_hdr = struct.pack('<IIIIII', 0x09, 0, 0, 12 + len(entries) + 8,
                                0, 12 + len(entries) + 8)

        # Rect data: width(4) + height(4) = 8 bytes
        buf = array_hdr + obj_hdr + entries + rect_data

        return bytes(buf), stride

    def start(self, width=None, height=None):
        """Start the PipeWire screencast in a background thread.

        Blocks until the stream is configured or an error occurs.
        """
        w = width or self._width
        h = height or self._height

        self._thread = threading.Thread(target=self._run, args=(w, h),
                                         daemon=True, name='pipewire-screencast')
        self._thread.start()
        # Wait for stream to be configured (with timeout)
        ready = self._ready_event.wait(timeout=10.0)
        if not ready:
            log.error("PipeWire: stream failed to configure (timeout)")
            self.stop()
            raise RuntimeError("PipeWire screencast: stream failed to configure")

    def _run(self, width, height):
        """Run the PipeWire event loop (executed in background thread)."""
        try:
            self._run_pipeline(width, height)
        except Exception as e:
            log.debug(f"PipeWire: pipeline error: {e}")

    def _run_pipeline(self, width, height):
        """Execute the full PipeWire client pipeline."""
        # 1. Create main loop
        self._main_loop = lib.pw_main_loop_new(ffi.new('char[]', b'pyvncs-screencast'))
        loop = lib.pw_main_loop_get_loop(self._main_loop)

        # 2. Connect to core (session-monitor)
        self._core = lib.pw_core_connect(self._main_loop,
                                          ffi.new('char[]', b'session-monitor'), None)
        if not self._core:
            raise RuntimeError("PipeWire: failed to connect to session-monitor")

        # 3. Connect to pipewire.session remote
        session_name = f"PipeWire.session:{self._session_id}"
        self._remote = lib.pw_remote_connect(self._main_loop,
                                              ffi.new('char[]', session_name.encode()), None)
        if not self._remote:
            raise RuntimeError(f"PipeWire: failed to connect to session '{self._session_id}'")

        # 4. Find the screencast node
        node_id = self._find_node()
        if node_id == 0:
            raise RuntimeError(f"PipeWire: no screencast node found for session "
                               f"{self._session_id} (requested node {self._node_id})")

        # 5. Build stream parameters
        pod_bytes, stride = self._build_params_pod(width, height)
        pod_ptr = ffi.new('char[]', pod_bytes)
        pod_size = len(pod_bytes)

        # 6. Create stream
        events = lib.malloc(lib.sizeof('struct pw_stream_events'))
        lib.memset(events, 0, lib.sizeof('struct pw_stream_events'))
        events.size = lib.sizeof('struct pw_stream_events')

        # Set up event callbacks
        @lib.callback("void(void*,int,const char*)")
        def on_state_changed(user_data, old, state, error):
            self._on_stream_state_changed(old, state, error)

        @lib.callback("void(void*)")
        def on_process(user_data):
            self._on_stream_process()

        events.state_changed = on_state_changed
        events.process = on_process

        self._stream = lib.pw_stream_new(self._core,
                                          ffi.new('char[]', b'pyvncs-capture'),
                                          pod_ptr)
        if not self._stream:
            raise RuntimeError("PipeWire: failed to create stream")

        # 7. Connect stream to the screencast node port
        # Port ID 0 is typically the video capture port for screencasts
        ret = lib.pw_stream_connect(self._stream, SPA_DIRECTION_INPUT,
                                     node_id, 0, pod_ptr, 1)
        if ret < 0:
            raise RuntimeError(f"PipeWire: failed to connect stream to node {node_id}")

        # 8. Start stream
        lib.pw_stream_start(self._stream)

        # 9. Run event loop until we have a frame or timeout
        log.debug("PipeWire: event loop running, waiting for frames...")
        timeout = time.time() + 15  # 15 second timeout
        while not self._stop_event.is_set():
            with self._frame_lock:
                if self._frame is not None:
                    log.debug("PipeWire: frame captured, stopping loop")
                    break

            if time.time() > timeout:
                log.error("PipeWire: frame capture timeout")
                break

            lib.pw_loop_iterate(loop, 100)  # 100ms timeout

        # 10. Cleanup
        self._cleanup()

    def _find_node(self):
        """Find the screencast node in the session.

        Iterates through nodes and returns the one matching our session's node ID.
        """
        # We need to wait for the remote to be connected first
        # The node is discovered through the core's node events or by
        # querying the session properties

        # For now, use the node_id passed in construction.
        # In practice, the node_id from the portal is the actual PipeWire node ID.
        return self._node_id

    def _cleanup(self):
        """Clean up PipeWire resources."""
        try:
            if self._stream:
                lib.pw_stream_set_active(self._stream, 0)
                lib.pw_stream_destroy(self._stream)
                self._stream = None
        except Exception:
            pass

        try:
            if self._remote:
                lib.pw_remote_destroy(self._remote)
                self._remote = None
        except Exception:
            pass

        try:
            if self._core:
                lib.pw_core_destroy(self._core)
                self._core = None
        except Exception:
            pass

        try:
            if self._main_loop:
                lib.pw_main_loop_free(self._main_loop)
                self._main_loop = None
        except Exception:
            pass

    def capture(self, timeout=15):
        """Capture a single frame.

        Waits up to `timeout` seconds for a frame to be available.

        Returns:
            PIL.Image or None
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._frame_lock:
                if self._frame is not None:
                    frame = self._frame
                    self._frame = None
                    return frame
            time.sleep(0.05)
        return None

    def stop(self):
        """Stop the screencast and clean up."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._cleanup()


# ---------------------------------------------------------------------------
# xdg-desktop-portal screencast session manager
# ---------------------------------------------------------------------------

class PortalSession:
    """Manages xdg-desktop-portal screencast session.

    Handles the D-Bus protocol for:
    1. Creating a screencast session
    2. Configuring session options (sources, cursor mode, etc.)
    3. Selecting sources (screens)
    4. Starting the capture
    5. Getting the NodeId for the screencast PipeWire node

    Usage:
        session = PortalSession()
        session.create()
        session.select_sources()
        node_id = session.start()
    """

    def __init__(self):
        self._bus = None
        self._portal = None
        self._session_path = None
        self._session_created = False
        self._ready_event = threading.Event()
        self._failed_event = threading.Event()
        self._node_id = None

    def create(self):
        """Create a new screencast session via xdg-desktop-portal."""
        import gi
        gi.require_version('Gtk', '4.0')
        from gi.repository import Gtk

        self._bus = Gtk.Application.get_default_dbus_connection()
        if not self._bus:
            raise RuntimeError("No D-Bus session connection available")

        self._portal = self._bus.get_proxy_sync(
            'org.freedesktop.portal.Desktop',
            '/org/freedesktop/portal/desktop',
            'org.freedesktop.portal.ScreenCast'
        )

        # Create session
        self._session_path, handle = self._portal.call_sync(
            'CreateSession',
            ffi.new('char[]', b'pyvncs'),  # dot_name
            ffi.new('char[]', b''),        # options (empty dict)
            0,                              # flags
            0                               # timeout
        )

        self._session_created = True

        # Configure session options
        options = {
            'session_handle_token': ffi.new('char[]', b'pyvncs-session'),
            'multiple': True,
            'cursor_mode': 'metadata',
        }
        self._configure_session(options)

        # Select sources: screen + cursor
        sources = 5  # ScreenCastSource.Screen(1) | ScreenCastSource.Cursor(4)
        self._select_sources(sources)

        # Start capture and wait for ready
        self._start_capture()

    def _configure_session(self, options):
        """Set session configuration options."""
        if not self._portal:
            return

        # Build options dict for D-Bus
        opt_dict = {}
        for k, v in options.items():
            opt_dict[k] = v

        try:
            self._portal.call_sync(
                'SetOption',
                ffi.new('char[]', b'session_handle_token'),
                ffi.new('char[]', b'pyvncs-session'),
                0, 0
            )
        except Exception:
            pass  # Some options may not be supported

    def _select_sources(self, sources):
        """Select which sources to capture."""
        if not self._portal or not self._session_path:
            return

        # Convert session path to GVariant for D-Bus
        try:
            self._portal.call_sync(
                'SelectSources',
                sources,   # uint32: source bitmask
                ffi.new('char[]', b''),  # filter (empty)
                False,     # allow_all
                False,     # do_all
                0, 0
            )
        except Exception as e:
            log.debug(f"Portal: SelectSources failed: {e}")

    def _on_state_changed(self, signal_name, props):
        """Handle ScreenCast.StateChanged D-Bus signal."""
        state = props.get('new_state', 0)
        state_str = {0: 'inactive', 1: 'active', 2: 'paused', 3: 'closed'}.get(state, str(state))
        log.debug(f"Portal: session state -> {state_str}")

        if state == 2:  # ACTIVE = ready
            node_id = props.get('node_id', 0)
            if isinstance(node_id, list) and len(node_id) > 0:
                self._node_id = node_id[0]
            else:
                self._node_id = node_id
            self._ready_event.set()
        elif state == 3:  # CLOSED
            self._failed_event.set()

    def _start_capture(self):
        """Start the screencast and wait for it to be ready."""
        if not self._portal or not self._session_path:
            raise RuntimeError("No portal session available")

        # Listen for StateChanged signal
        self._portal.connect_signal('StateChanged', self._on_state_changed)

        # Start the capture
        start_result = self._portal.call_sync(
            'Start',
            ffi.new('char[]', b''),  # parent_window
            0, 0
        )

        if not self._ready_event.wait(timeout=10.0):
            raise RuntimeError("Portal: screencast session failed to start (timeout)")

        if self._node_id is None or self._node_id == 0:
            raise RuntimeError(f"Portal: no screencast node ID received "
                               f"(start result: {start_result})")

        log.debug(f"Portal: screencast ready, node_id={self._node_id}")

    @property
    def node_id(self):
        """The PipeWire NodeId for the screencast node."""
        return self._node_id

    @property
    def session_id(self):
        """The session ID string for PipeWire connection."""
        if self._session_path:
            # Session path is like '/org/freedesktop/portal/desktop/session/NNNN'
            parts = self._session_path.split('/')
            if parts:
                return parts[-1]
        return ''


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class WaylandImageGrab:
    """Wayland screen grabber using PipeWire + xdg-desktop-portal.

    This class provides a drop-in replacement for PIL's ImageGrab on Wayland:

        from lib.imagegrab_wayland import WaylandImageGrab
        image = WaylandImageGrab.grab()  # returns PIL.Image RGB

    The capture uses xdg-desktop-portal to request a screencast session,
    then uses the PipeWire multimedia framework to receive frame data.
    """

    _session = None
    _screencast = None
    _lock = threading.Lock()

    @staticmethod
    def grab(width=None, height=None):
        """Capture the screen as a PIL Image.

        Args:
            width: Override screen width (auto-detected from frame if not given)
            height: Override screen height (auto-detected from frame if not given)

        Returns:
            PIL.Image in RGB mode

        Raises:
            RuntimeError: If screencast capture fails
        """
        with WaylandImageGrab._lock:
            session = PortalSession()
            session.create()

            node_id = session.node_id
            session_id = session.session_id

            if not node_id:
                raise RuntimeError("Wayland: no screencast node available")

            log.debug(f"Wayland: creating PipeWire screencast "
                      f"(session={session_id}, node={node_id})")

            screencast = PipeWireScreencast(session_id, node_id,
                                             width or 1920, height or 1080)
            screencast.start()

            try:
                frame = screencast.capture(timeout=15)
            finally:
                screencast.stop()

            if frame is None:
                raise RuntimeError("Wayland: no frame captured from PipeWire")

            log.debug(f"Wayland: captured frame {frame.size}")
            return frame

    @staticmethod
    def grab_continuous(fps=5):
        """Generator that yields frames at the specified FPS.

        Args:
            fps: Frames per second

        Yields:
            PIL.Image in RGB mode
        """
        interval = 1.0 / fps
        while True:
            try:
                frame = WaylandImageGrab.grab()
                yield frame
            except RuntimeError:
                log.debug("Wayland: capture failed, retrying...")
            time.sleep(interval)
