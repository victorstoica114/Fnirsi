/* Hardware-free integration test of the actual isolated USB GSource code. */
#include <config.h>
#include <stdio.h>
#include <glib.h>
#include <libusb.h>
#include <libsigrok/libsigrok.h>
#include "libsigrok-internal.h"

static unsigned int callbacks, finalized, checks;
static const struct libusb_pollfd **LIBUSB_CALL no_pollfds(libusb_context *ctx)
{ (void)ctx; return NULL; }
static int LIBUSB_CALL no_usb_timeout(libusb_context *ctx, struct timeval *tv)
{ (void)ctx; (void)tv; return 0; }
static void LIBUSB_CALL no_pollfd_notifiers(libusb_context *ctx,
    libusb_pollfd_added_cb add, libusb_pollfd_removed_cb remove, void *data)
{ (void)ctx; (void)add; (void)remove; (void)data; }
static void LIBUSB_CALL free_fake_pollfds(const struct libusb_pollfd **fds)
{ (void)fds; }
static int source_destroyed(struct sr_session *session, void *key, GSource *source)
{ (void)session; (void)key; (void)source; finalized++; return SR_OK; }

#define libusb_get_pollfds no_pollfds
#define libusb_get_next_timeout no_usb_timeout
#define libusb_set_pollfd_notifiers no_pollfd_notifiers
#define libusb_free_pollfds free_fake_pollfds
#define sr_session_source_destroyed source_destroyed
#include "usb-source-under-test.h"
#undef sr_session_source_destroyed
#undef libusb_free_pollfds
#undef libusb_set_pollfd_notifiers
#undef libusb_get_next_timeout
#undef libusb_get_pollfds

static gboolean timer_callback(int fd, int revents, void *data)
{
    (void)data;
    g_assert_cmpint(fd, ==, -1);
    g_assert_cmpint(revents, ==, 0);
    callbacks++;
    return callbacks < 3;
}

static void check_timeout(int requested_ms, int expected_us)
{
    struct sr_session session = {0};
    GMainContext *context = g_main_context_new();
    GSource *source = usb_source_new(&session, (libusb_context *)(uintptr_t)1,
                                    requested_ms);
    struct usb_source *usb = (struct usb_source *)source;
    callbacks = finalized = 0;
    g_assert_nonnull(source);
    g_assert_cmpint(usb->timeout_us, ==, expected_us);
    g_assert_cmpuint(usb->pollfds->len, ==, 0);
    g_source_set_callback(source, G_SOURCE_FUNC(timer_callback), NULL, NULL);
    g_assert_cmpuint(g_source_attach(source, context), >, 0);
    g_source_unref(source);
    while (callbacks < 3)
        g_main_context_iteration(context, TRUE);
    g_assert_cmpuint(callbacks, ==, 3);
    g_assert_cmpuint(finalized, ==, 1);
    g_main_context_unref(context);
    checks += 6;
}

int main(void)
{
    check_timeout(50, 2000);
    check_timeout(-1, 2000);
    check_timeout(0, 2000);
    check_timeout(1, 1000);
    printf("PASS: %u checks; actual Windows USB GSource, timer dispatch/rearm/finalize; no hardware accessed.\n", checks);
    return 0;
}
