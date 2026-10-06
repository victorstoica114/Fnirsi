/* SPDX-License-Identifier: GPL-3.0-only */
#define main inherited_fixture_main
#include "../artifacts/dla32-wch-worker-v9-tests/test_dla32_worker_v9_unit.c"
#undef main

static LONG upload_active, fail_upload_disable, fail_upload_enable, fail_upload_clear;
static DWORD upload_failure_error;
static LONG upload_order_errors;

static BOOL WINAPI checked_upload_set(ULONG index,ULONG enable,ULONG pipe,ULONG length)
{
    (void)index; (void)pipe; (void)length;
    enter_io();
    if ((enable && fail_upload_enable) || (!enable && armed && fail_upload_disable)) {
        leave_io();SetLastError(upload_failure_error);return FALSE;
    }
    upload_active=enable!=0;
    leave_io();SetLastError(ERROR_SUCCESS);return TRUE;
}
static BOOL WINAPI checked_upload_clear(ULONG index,ULONG pipe)
{
    (void)index;(void)pipe;enter_io();
    if (!upload_active) upload_order_errors++;
    leave_io();SetLastError(fail_upload_clear ? ERROR_GEN_FAILURE : ERROR_SUCCESS);
    return !fail_upload_clear;
}
static BOOL WINAPI checked_upload_write(ULONG index,PVOID data,PULONG length)
{
    const uint8_t *packet=data;
    if ((*length>9 && packet[9]==0x15 && upload_active) ||
        (*length>9 && packet[9]==0x12 && !upload_active)) upload_order_errors++;
    return fake_write(index,data,length);
}
static void upload_fixture(struct fixture *f)
{
    fixture_init(f,FALSE);upload_active=fail_upload_disable=fail_upload_enable=fail_upload_clear=0;
    upload_order_errors=0;upload_failure_error=ERROR_GEN_FAILURE;
    wch_api.set_buf_upload_ex=checked_upload_set;wch_api.clear_buf_upload=checked_upload_clear;
    wch_api.write=checked_upload_write;
}
static void test_upload_start_failure(gboolean clear)
{
    struct fixture f;
    upload_fixture(&f);
    if (clear) fail_upload_clear=TRUE; else fail_upload_enable=TRUE;
    expect("failed upload initialization rejects capture before ARM",dev_acquisition_start(f.sdi)!=SR_OK &&
        !atomic_read(&commands[0x12]) && !atomic_read(&sample_reads));
    expect("upload startup rollback retires queue and application resources",
        !upload_active && !f.devc->wch.upload_dirty && !f.devc->worker && !sources && ends==1);
    expect("startup never STOPs while upload queue is active",!upload_order_errors);
    fail_upload_clear=fail_upload_enable=FALSE;fixture_clear(&f);
}
static void test_order_detector(void)
{
    struct fixture f;uint8_t packet[DLA_COMMAND_SIZE];ULONG length=sizeof(packet);
    upload_fixture(&f);upload_active=TRUE;dla_stop(packet);
    checked_upload_write(0,packet,&length);
    expect("ordering detector catches STOP before SDK retirement",upload_order_errors==1);
    upload_active=FALSE;dla_channels(packet);length=sizeof(packet);
    checked_upload_write(0,packet,&length);
    expect("ordering detector catches ARM before SDK upload enable",upload_order_errors==2);
    fixture_clear(&f);
}
static void test_upload_retirement_failure(gboolean lost)
{
    struct fixture f;unsigned int before;
    upload_fixture(&f);scripts[0]=(struct script){32,TRUE,ERROR_SUCCESS,TRUE};script_length=1;
    expect("upload worker starts with SDK queue before ARM",dev_acquisition_start(f.sdi)==SR_OK &&
        upload_active && WaitForSingleObject(read_entered,1000)==WAIT_OBJECT_0);
    fail_upload_disable=TRUE;upload_failure_error=lost ? ERROR_DEVICE_NOT_CONNECTED : ERROR_GEN_FAILURE;
    dev_acquisition_stop(f.sdi);SetEvent(read_release);inspect_end=TRUE;
    expect("failed worker queue retirement still reconciles result and native exit",pump_until(&f,NULL,TRUE) &&
        ends==1 && f.devc->worker_unconsumed_bytes==32 && end_guard_ok);
    expect("SDK disable failure defers hardware STOP and retains both ownership flags",
        f.devc->wch.upload_dirty && f.devc->upload_stop_pending && atomic_read(&commands[0x15])==1);
    before=atomic_read(&sdk_reads)+atomic_read(&sdk_writes);
    if (lost) {
        expect("explicit lost-device retirement closes stale host handle without USB retry",dev_close(f.sdi)==SR_OK &&
            !f.devc->wch.upload_dirty && !f.devc->upload_stop_pending && sdk_closes==1 &&
            before==(unsigned int)(atomic_read(&sdk_reads)+atomic_read(&sdk_writes)));
    } else {
        expect("ordinary disable failure refuses close and preserves SDK handle",dev_close(f.sdi)!=SR_OK &&
            f.devc->wch.opened && !sdk_closes);
        fail_upload_disable=FALSE;
        expect("accepted retirement recovery disables before STOP then closes",dev_close(f.sdi)==SR_OK &&
            !f.devc->wch.upload_dirty && !f.devc->upload_stop_pending && !upload_active && sdk_closes==1 &&
            atomic_read(&commands[0x15])==2);
    }
    expect("upload retirement preserves SDK ownership and command ordering",!upload_order_errors);
    fail_upload_disable=FALSE;fixture_clear(&f);
}
int main(void)
{
    if (inherited_fixture_main())return 1;
    checks=failures=0;
    test_order_detector();
    test_upload_start_failure(FALSE);test_upload_start_failure(TRUE);
    test_upload_retirement_failure(FALSE);test_upload_retirement_failure(TRUE);
    printf("Upload worker audit: %u checks, %u failures.\n",checks,failures);
    return failures ? 1 : 0;
}
