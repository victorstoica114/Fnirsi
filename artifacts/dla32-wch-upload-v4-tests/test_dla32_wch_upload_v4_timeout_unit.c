/* SPDX-License-Identifier: GPL-3.0-only */
/* The real Windows transport helpers, exercised through fake SDK callbacks.
 * No DLL loads, device opens, or real USB API calls are permitted. */
#include <config.h>
#include <glib.h>
#include <libsigrok/libsigrok.h>
#include "libsigrok-internal.h"
#include <windows.h>

static unsigned int checks, failures, setter_calls, read_calls, write_calls;
static unsigned int load_calls, bad_arguments, tuple[4], expected_read_ms, expected_pipe;
static gboolean setter_fail, read_ok, write_ok, model_reply;
static DWORD read_error;
static ULONG returned_read, returned_write;
static int events[64], event_count;
static HMODULE WINAPI blocked_load(LPCWSTR name, HANDLE file, DWORD flags)
{ (void)name; (void)file; (void)flags; load_calls++; return NULL; }
#define LoadLibraryExW blocked_load
#define LOG_PREFIX "fnirsi-dla32-timeout-test"
#include "D:/Documente/analizor logic/artifacts/dla32-wch-upload-v4-source/libsigrok/src/hardware/fnirsi-dla32/transport-wch.h"
#undef LoadLibraryExW
#undef LOG_PREFIX

static void record(int event)
{ if (event_count < (int)ARRAY_SIZE(events)) events[event_count++] = event; }
static BOOL WINAPI fake_timeout_ex(ULONG index, ULONG t0, ULONG t1, ULONG t2, ULONG t3)
{
    setter_calls++; record(10); tuple[0]=t0; tuple[1]=t1; tuple[2]=t2; tuple[3]=t3;
    if (index != 7 || t0 != t1 || t0 != t2 || t0 != t3) bad_arguments++;
    if (setter_fail) { SetLastError(ERROR_GEN_FAILURE); return FALSE; }
    return TRUE;
}
static BOOL WINAPI fake_timeout(ULONG index, ULONG t0, ULONG t1)
{
    setter_calls++; record(11); tuple[0]=t0; tuple[1]=t1; tuple[2]=tuple[3]=0;
    if (index != 7 || t0 != t1) bad_arguments++;
    if (setter_fail) { SetLastError(ERROR_GEN_FAILURE); return FALSE; }
    return TRUE;
}
static BOOL WINAPI fake_read(ULONG index, ULONG pipe, PVOID data, PULONG length)
{
    read_calls++; record(20);
    if (index != 7 || pipe != expected_pipe || tuple[0] != expected_read_ms) bad_arguments++;
    if (model_reply) {
        uint8_t *reply = data;
        memset(reply, 0, *length); reply[0]=0x0a; reply[1]=0x02; reply[2]=0x0d;
        memcpy(reply+13, "DL32", 4); reply[18]=0x0b;
    }
    *length = returned_read;
    SetLastError(read_error);
    return read_ok;
}
static BOOL WINAPI fake_write(ULONG index, PVOID data, PULONG length)
{
    (void)data; write_calls++; record(30);
    if (index != 7 || tuple[0] != 1000 || tuple[1] != 1000 ||
        (wch_api.timeout_ex && (tuple[2] != 1000 || tuple[3] != 1000))) bad_arguments++;
    if (returned_write != G_MAXUINT32) *length=returned_write;
    if (!write_ok) SetLastError(ERROR_GEN_FAILURE);
    return write_ok;
}
static void expect(const char *label, gboolean condition)
{
    checks++; if (!condition) failures++;
    printf("%s: %s\n", condition ? "PASS" : "FAIL", label);
}
static void reset(gboolean legacy)
{
    memset(&wch_api, 0, sizeof(wch_api));
    wch_api.timeout_ex=legacy ? NULL : fake_timeout_ex;
    wch_api.timeout=fake_timeout; wch_api.read=fake_read; wch_api.write=fake_write;
    setter_calls=read_calls=write_calls=bad_arguments=0; event_count=0;
    setter_fail=FALSE; read_ok=write_ok=TRUE; model_reply=FALSE;
    returned_read=32; returned_write=G_MAXUINT32;
    read_error=ERROR_SUCCESS; expected_read_ms=20; expected_pipe=1;
    memset(tuple,0,sizeof(tuple));
}
static void test_switches(gboolean legacy)
{
    struct dla_wch wch={.index=7,.opened=TRUE};
    unsigned int deadlines[]={20,50,100,1000}, i;
    uint8_t data[128]={0}; int actual;
    reset(legacy);
    for (i=0;i<ARRAY_SIZE(deadlines);i++) {
        expected_read_ms=deadlines[i]; actual=12345;
        expect("ReadEndP observes requested direct deadline before I/O",
            dla_wch_read(&wch,1,data,sizeof(data),&actual,expected_read_ms)==LIBUSB_SUCCESS
            && actual==32 && tuple[0]==expected_read_ms && !bad_arguments);
        expect("Every WriteData restores all timeout fields to 1000ms after short/long read",
            dla_wch_write(&wch,data,sizeof(data))==SR_OK && tuple[0]==1000
            && tuple[1]==1000 && !bad_arguments);
    }
    expect("Every operation configures timeout exactly once before its I/O",
        setter_calls==8 && read_calls==4 && write_calls==4 && event_count==16);
    for (i=0;i<4;i++)
        expect("Read/write SDK ordering is setter-read-setter-write",
            events[4*i]==(legacy?11:10) && events[4*i+1]==20 &&
            events[4*i+2]==(legacy?11:10) && events[4*i+3]==30);
    tuple[0]=tuple[1]=20;
    expect("Consecutive command writes cannot reuse a stale global short deadline",
        dla_wch_write(&wch,data,sizeof(data))==SR_OK && setter_calls==9 && !bad_arguments);
}
static void test_failures(gboolean legacy)
{
    struct dla_wch wch={.index=7,.opened=TRUE};
    uint8_t data[128]={0}; int actual=777;
    reset(legacy); setter_fail=TRUE;
    expect("Failed read timeout preparation blocks ReadEndP and initializes actual0",
        dla_wch_read(&wch,1,data,sizeof(data),&actual,20)==LIBUSB_ERROR_IO
        && actual==0 && !read_calls && setter_calls==1);
    expect("Failed command timeout preparation blocks WriteData",
        dla_wch_write(&wch,data,sizeof(data))==SR_ERR_IO && !write_calls && setter_calls==2);
    setter_fail=FALSE;
    expect("Command can retry after failed timeout preparation with explicit 1000ms restore",
        dla_wch_write(&wch,data,sizeof(data))==SR_OK && write_calls==1 && !bad_arguments);
    wch.opened=FALSE;
    expect("Closed read never changes timeout or calls ReadEndP",
        dla_wch_read(&wch,1,data,sizeof(data),&actual,20)==LIBUSB_ERROR_NO_DEVICE
        && actual==0 && setter_calls==3 && !read_calls);
    expect("Closed write never changes timeout or calls WriteData",
        dla_wch_write(&wch,data,sizeof(data))==SR_ERR_DEV_CLOSED && setter_calls==3 && write_calls==1);
}
static void test_payload_contract(void)
{
    struct dla_wch wch={.index=7,.opened=TRUE};
    uint8_t data[128]={0}; int actual;
    reset(FALSE); returned_read=129;
    expect("Successful SDK overlength read is still overflow with actual0",
        dla_wch_read(&wch,1,data,sizeof(data),&actual,20)==LIBUSB_ERROR_OVERFLOW && actual==0);
    read_ok=FALSE; returned_read=128; read_error=ERROR_TIMEOUT;
    expect("Timeout with unchanged requested length never exposes stale bytes",
        dla_wch_read(&wch,1,data,sizeof(data),&actual,20)==LIBUSB_ERROR_TIMEOUT && actual==0);
    returned_read=7;
    expect("Partial timeout retains only the SDK-reported shorter length",
        dla_wch_read(&wch,1,data,sizeof(data),&actual,20)==LIBUSB_ERROR_TIMEOUT && actual==7);
    read_error=ERROR_GEN_FAILURE;
    expect("Non-timeout SDK failure remains I/O error with existing partial-count semantics",
        dla_wch_read(&wch,1,data,sizeof(data),&actual,20)==LIBUSB_ERROR_IO && actual==7);
    write_ok=TRUE; returned_write=127;
    expect("Short successful command write is still rejected",
        dla_wch_write(&wch,data,sizeof(data))==SR_ERR_IO);
    write_ok=FALSE; returned_write=0;
    expect("SDK command write failure is still rejected",
        dla_wch_write(&wch,data,sizeof(data))==SR_ERR_IO);
    expect("Failure/overflow tests used only correct timeout tuples", !bad_arguments);
}
static void test_identification(void)
{
    struct dla_wch wch={.index=7,.opened=TRUE};
    reset(FALSE); expected_read_ms=500; expected_pipe=2; model_reply=TRUE; returned_read=19;
    expect("Identification uses WriteData1000 then pipe2 ReadEndP500 through actual helpers",
        dla_wch_model32(&wch) && setter_calls==2 && read_calls==1 && write_calls==1
        && !bad_arguments && events[0]==10 && events[1]==30 && events[2]==10 && events[3]==20);
}
int main(void)
{
    setvbuf(stdout,NULL,_IONBF,0); sr_log_loglevel_set(SR_LOG_NONE);
    test_switches(FALSE); test_switches(TRUE);
    test_failures(FALSE); test_failures(TRUE);
    test_payload_contract(); test_identification();
    expect("No real SDK load, device open or hardware operation occurred", !load_calls);
    printf("Timeout audit: %u checks, %u failures. No hardware accessed.\n", checks, failures);
    return failures?1:0;
}
