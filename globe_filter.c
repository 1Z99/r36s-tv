/* RGBA scaler/projector; no SDL, heap allocation, or alignment requirement. */
#include <limits.h>
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define TV_MAX_DIM 8192
#define TV_MAX_PROJECT 2048

/* Pixel centers, with the 8-bit fractional coordinate rounded down before clamping. */
static inline int coordinate(int pixel, int source, int destination)
{
    int64_t value = ((2 * (int64_t)pixel + 1) * source * 128) / destination - 128;
    int limit = (source - 1) * 256;
    return value < 0 ? 0 : value > limit ? limit : (int)value;
}

static inline uint32_t load_pixel(const uint8_t *pixel)
{
    uint32_t value;
    memcpy(&value, pixel, sizeof(value));
    return value;
}

static inline uint32_t blend(uint32_t a, uint32_t b, unsigned weight)
{
    /* Each 16-bit lane holds one channel's weighted sum, at most 255 * 256. */
    const uint32_t mask = UINT32_C(0x00ff00ff);
    uint32_t rb = (((a & mask) * (256 - weight) + (b & mask) * weight) >> 8) & mask;
    uint32_t ga = ((((a >> 8) & mask) * (256 - weight)
                   + ((b >> 8) & mask) * weight) >> 8) & mask;
    return rb | (ga << 8);
}

static int valid_buffers(const uint8_t *src, int sw, int sh, int src_pitch,
                         uint8_t *dst, int dw, int dh, int dst_pitch)
{
    if (!src || !dst || sw < 1 || sh < 1 || dw < 1 || dh < 1
        || sw > TV_MAX_DIM || sh > TV_MAX_DIM || dw > TV_MAX_DIM || dh > TV_MAX_DIM
        || src_pitch < sw * 4 || dst_pitch < dw * 4)
        return 0;

    uint64_t src_size = (uint64_t)(sh - 1) * src_pitch + sw * 4;
    uint64_t dst_size = (uint64_t)(dh - 1) * dst_pitch + dw * 4;
    if (src_size > PTRDIFF_MAX || dst_size > PTRDIFF_MAX)
        return 0;
    uintptr_t s = (uintptr_t)src, d = (uintptr_t)dst;
    if (s <= d ? d - s < src_size : s - d < dst_size)
        return 0;
    return 1;
}

/* Return 0 on success, -1 for invalid arguments. Buffers must not overlap and
 * must cover (height - 1) * pitch + width * 4 bytes. Padding is never touched.
 * Each channel, including alpha, is interpolated horizontally then vertically,
 * truncating after each pass. Input/output are straight RGBA, not premultiplied.
 */
int tv_globe_scale(const uint8_t *src, int sw, int sh, int src_pitch,
                   uint8_t *dst, int dw, int dh, int dst_pitch)
{
    struct x_sample { uint16_t left, right, weight; } columns[TV_MAX_DIM];
    if (!valid_buffers(src, sw, sh, src_pitch, dst, dw, dh, dst_pitch))
        return -1;
    for (int x = 0; x < dw; ++x) {
        int fixed = coordinate(x, sw, dw);
        int left = fixed >> 8;
        columns[x].left = (uint16_t)(left * 4);
        columns[x].right = (uint16_t)((left + (left < sw - 1)) * 4);
        columns[x].weight = (uint16_t)(fixed & 255);
    }
    for (int y = 0; y < dh; ++y) {
        int fixed = coordinate(y, sh, dh);
        int top = fixed >> 8;
        unsigned weight = (unsigned)(fixed & 255);
        const uint8_t *row0 = src + (size_t)top * src_pitch;
        const uint8_t *row1 = row0 + (top < sh - 1 ? src_pitch : 0);
        uint8_t *out = dst + (size_t)y * dst_pitch;
        for (int x = 0; x < dw; ++x) {
            unsigned left = columns[x].left, right = columns[x].right;
            unsigned wx = columns[x].weight;
            uint32_t a = blend(load_pixel(row0 + left), load_pixel(row0 + right), wx);
            uint32_t b = blend(load_pixel(row1 + left), load_pixel(row1 + right), wx);
            uint32_t pixel = blend(a, b, weight);
            memcpy(out + (size_t)x * 4, &pixel, sizeof(pixel));
        }
    }
    return 0;
}

/* The optional projector ABI is independent of the already shipped scaler. */
int tv_globe_project_abi(void)
{
    return 1;
}

/* Orthographic inverse projection of a single, undoubled equirectangular atlas.
 * Same buffer contract as scale; output size 1..2048, angles in degrees.
 */
int tv_globe_project(const uint8_t *src, int sw, int sh, int src_pitch,
                     uint8_t *dst, int size, int dst_pitch,
                     float latitude, float longitude, float zoom)
{
    if (size < 1 || size > TV_MAX_PROJECT
        || !isfinite(latitude) || latitude < -90.0f || latitude > 90.0f
        || !isfinite(longitude) || !isfinite(zoom) || zoom <= 0.0f || zoom > 64.0f
        || !valid_buffers(src, sw, sh, src_pitch, dst, size, size, dst_pitch))
        return -1;

    const float pi = 3.14159265358979323846f;
    float lat = latitude * (pi / 180.0f);
    float lon = remainderf(longitude, 360.0f) * (pi / 180.0f);
    float slat = sinf(lat), clat = cosf(lat), slon = sinf(lon), clon = cosf(lon);
    float u_scale = sw / (2.0f * pi), v_scale = sh / pi;
    /* Double here keeps tiny positive float zooms safe, including at the center. */
    double radius = 150.0 * size / 350.0 * zoom;
    float xs[TV_MAX_PROJECT], x_squared[TV_MAX_PROJECT];
    for (int x = 0; x < size; ++x) {
        double local = (x + 0.5 - size / 2.0) / radius;
        xs[x] = local < -1.0 || local > 1.0 ? 2.0f : (float)local;
        x_squared[x] = xs[x] * xs[x];
    }
    const uint8_t background[4] = {9, 10, 12, 255};
    uint32_t outside = load_pixel(background);
    for (int y = 0; y < size; ++y) {
        double local_y = (size / 2.0 - y - 0.5) / radius;
        float py = local_y < -1.0 || local_y > 1.0 ? 2.0f : (float)local_y;
        float remaining = 1.0f - py * py;
        uint8_t *out = dst + (size_t)y * dst_pitch;
        for (int x = 0; x < size; ++x) {
            uint32_t pixel = outside;
            float depth_squared = remaining - x_squared[x];
            if (depth_squared >= 0.0f) {
                float px = xs[x], depth = sqrtf(depth_squared);
                float north = clat * py + slat * depth;
                float forward_local = -slat * py + clat * depth;
                float east = clon * px + slon * forward_local;
                float forward = -slon * px + clon * forward_local;
                north = north < -1.0f ? -1.0f : north > 1.0f ? 1.0f : north;
                /* Shift U by half a pixel, wrapping both neighbors at the seam. */
                float u = atan2f(east, forward) * u_scale + sw * 0.5f - 0.5f;
                float v = sh * 0.5f - asinf(north) * v_scale - 0.5f;
                if (u < 0.0f)
                    u += sw;
                int x0 = (int)u;
                unsigned wx = (unsigned)((u - x0) * 256.0f);
                if (x0 >= sw)
                    x0 = 0;
                int x1 = x0 + 1 < sw ? x0 + 1 : 0;
                v = v < 0.0f ? 0.0f : v > sh - 1 ? (float)(sh - 1) : v;
                int y0 = (int)v, y1 = y0 + (y0 < sh - 1);
                unsigned wy = (unsigned)((v - y0) * 256.0f);
                const uint8_t *row0 = src + (size_t)y0 * src_pitch;
                const uint8_t *row1 = src + (size_t)y1 * src_pitch;
                uint32_t a = blend(load_pixel(row0 + x0 * 4), load_pixel(row0 + x1 * 4), wx);
                uint32_t b = blend(load_pixel(row1 + x0 * 4), load_pixel(row1 + x1 * 4), wx);
                pixel = blend(a, b, wy);
            }
            memcpy(out + (size_t)x * 4, &pixel, sizeof(pixel));
        }
    }
    return 0;
}

int tv_globe_borders_abi(void)
{
    return 1;
}

static int clip_border(double *x0, double *y0, double *x1, double *y1, int w, int h)
{
    double dx = *x1 - *x0, dy = *y1 - *y0, low = 0.0, high = 1.0;
    double p[4] = {-dx, dx, -dy, dy};
    double q[4] = {*x0, w - 1.0 - *x0, *y0, h - 1.0 - *y0};
    for (int edge = 0; edge < 4; ++edge) {
        if (p[edge] == 0.0) {
            if (q[edge] < 0.0)
                return 0;
        } else {
            double t = q[edge] / p[edge];
            if (p[edge] < 0.0) {
                if (t > high)
                    return 0;
                if (t > low)
                    low = t;
            } else {
                if (t < low)
                    return 0;
                if (t < high)
                    high = t;
            }
        }
    }
    double values[4] = {*x0 + low * dx, *y0 + low * dy,
                        *x0 + high * dx, *y0 + high * dy};
    for (int i = 0; i < 4; ++i) {
        double limit = i % 2 ? h - 1.0 : w - 1.0;
        values[i] = values[i] < 0.0 ? 0.0 : values[i] > limit ? limit : values[i];
    }
    *x0 = values[0]; *y0 = values[1]; *x1 = values[2]; *y1 = values[3];
    return 1;
}

static inline void border_pixel(uint8_t *dst, int w, int h, int pitch,
                                 int x, int y, double coverage, uint32_t shade)
{
    if (x < 0 || x >= w || y < 0 || y >= h || coverage <= 0.0)
        return;
    unsigned weight = coverage >= 1.0 ? 256 : (unsigned)(coverage * 256.0 + 0.5);
    uint8_t *out = dst + (size_t)y * pitch + (size_t)x * 4;
    uint32_t pixel = blend(load_pixel(out), shade, weight);
    memcpy(out, &pixel, sizeof(pixel));
}

static void draw_border(uint8_t *dst, int w, int h, int pitch,
                         double x0, double y0, double x1, double y1, uint32_t shade)
{
    if (!clip_border(&x0, &y0, &x1, &y1, w, h))
        return;
    int steep = fabs(y1 - y0) > fabs(x1 - x0);
    if (steep) {
        double tmp = x0; x0 = y0; y0 = tmp;
        tmp = x1; x1 = y1; y1 = tmp;
    }
    if (x0 > x1) {
        double tmp = x0; x0 = x1; x1 = tmp;
        tmp = y0; y0 = y1; y1 = tmp;
    }
    if (x1 - x0 < 1e-12)
        return;
    double gradient = (y1 - y0) / (x1 - x0);
    int first = (int)floor(x0 + 0.5), last = (int)floor(x1 + 0.5);
    /* Wu coverage, including partial endpoint cells and subpixel-short segments.
     * Coordinates here refer to pixel centers, not pixel edges. */
    for (int x = first; x <= last; ++x) {
        double left = x0 > x - 0.5 ? x0 : x - 0.5;
        double right = x1 < x + 0.5 ? x1 : x + 0.5;
        double extent = right - left;
        double y = y0 + gradient * (x - x0);
        int base = (int)floor(y);
        double fraction = y - base;
        border_pixel(dst, w, h, pitch, steep ? base : x, steep ? x : base,
                     extent * (1.0 - fraction), shade);
        border_pixel(dst, w, h, pitch, steep ? base + 1 : x, steep ? x : base + 1,
                     extent * fraction, shade);
    }
}

/* In-place native-pixel overlay. XYZ endpoints are geographic unit vectors,
 * six floats per segment. Empty input is valid; input and output cannot overlap.
 * Validate the entire input before painting so errors leave output unchanged. */
int tv_globe_borders(const float *src_xyz, int count, uint8_t *dst,
                     int w, int h, int pitch, float latitude, float longitude, float zoom)
{
    if (!dst || w < 1 || w > TV_MAX_DIM || h < 1 || h > TV_MAX_DIM || pitch < w * 4
        || count < 0 || count > 200000 || (count && !src_xyz)
        || !isfinite(latitude) || latitude < -90.0f || latitude > 90.0f
        || !isfinite(longitude) || !isfinite(zoom) || zoom <= 0.0f || zoom > 64.0f)
        return -1;
    uint64_t dst_size = (uint64_t)(h - 1) * pitch + w * 4;
    if (dst_size > PTRDIFF_MAX)
        return -1;
    if (count) {
        uintptr_t s = (uintptr_t)src_xyz, d = (uintptr_t)dst;
        size_t src_size = (size_t)count * 6 * sizeof(float);
        if (s <= d ? d - s < src_size : s - d < dst_size)
            return -1;
        for (int i = 0; i < count * 6; ++i) {
            float value;
            memcpy(&value, (const uint8_t *)src_xyz + (size_t)i * sizeof(float), sizeof(value));
            if (!isfinite(value))
                return -1;
        }
    }
    if (!count)
        return 0;
    const double radians = 3.14159265358979323846 / 180.0;
    double lat = latitude * radians, lon = remainder(longitude, 360.0) * radians;
    double slat = sin(lat), clat = cos(lat), slon = sin(lon), clon = cos(lon);
    double rx = 150.0 * zoom * w / 350.0, ry = 150.0 * zoom * h / 350.0;
    const uint8_t color[4] = {64, 196, 104, 255};
    uint32_t shade = load_pixel(color);
    for (int i = 0; i < count; ++i) {
        float input[6];
        memcpy(input, (const uint8_t *)src_xyz + (size_t)i * sizeof(input), sizeof(input));
        double point[2][3];
        for (int end = 0; end < 2; ++end) {
            double x = input[end * 3], y = input[end * 3 + 1], z = input[end * 3 + 2];
            double depth = slon * x + clon * z;
            point[end][0] = clon * x - slon * z;
            point[end][1] = clat * y - slat * depth;
            point[end][2] = slat * y + clat * depth;
        }
        if (point[0][2] < 0.0 && point[1][2] < 0.0)
            continue;
        if (point[0][2] < 0.0 || point[1][2] < 0.0) {
            int behind = point[0][2] < 0.0 ? 0 : 1, front = 1 - behind;
            double t = point[front][2] / (point[front][2] - point[behind][2]);
            double x = point[front][0] + t * (point[behind][0] - point[front][0]);
            double y = point[front][1] + t * (point[behind][1] - point[front][1]);
            double length = sqrt(x * x + y * y);
            if (length < 1e-12)
                continue;
            point[behind][0] = x / length;
            point[behind][1] = y / length;
        }
        draw_border(dst, w, h, pitch,
                    w / 2.0 + point[0][0] * rx - 0.5, h / 2.0 - point[0][1] * ry - 0.5,
                    w / 2.0 + point[1][0] * rx - 0.5, h / 2.0 - point[1][1] * ry - 0.5, shade);
    }
    return 0;
}
