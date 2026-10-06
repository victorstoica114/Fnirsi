/* SPDX-License-Identifier: GPL-3.0-only */
/* Deep-ring FIFO wrap and held-buffer ownership with fake USB and real threads. */
#define main inherited_fixture_main
#include "../artifacts/dla32-wch-worker-v8-tests/test_dla32_wch_worker_v8_unit.c"
#undef main

static BOOL WINAPI ring_read(ULONG index, ULONG pipe, PVOID data, PULONG length)
{
    BOOL result=fake_read(index,pipe,data,length);
    if (pipe==1 && atomic_read(&armed)) {
        *length=32;
        memset(data,(uint8_t)atomic_read(&sample_reads),32);
    }
    return result;
}

int main(void)
{
    struct fixture f;
    struct dla_worker_result result;
    main_thread=GetCurrentThreadId();
    fixture_init(&f,FALSE); wch_api.read=ring_read;
    f.devc->limits.limit_samples=100000000;
    expect("ring starts",dev_acquisition_start(f.sdi)==SR_OK);
    expect("every configured slot fills without frontend consumption",
        wait_queued(f.devc->worker,DLA_WORKER_BUFFER_SLOTS));
    g_usleep(20000);
    expect("producer never reads beyond its bounded ring",
        atomic_read(&sample_reads)==DLA_WORKER_BUFFER_SLOTS);
    expect("first claimed identity is read one",dla_worker_take_result(f.devc->worker,&result) &&
        result.read_id==1 && result.count==32 && result.buffer[0]==1);
    g_usleep(20000);
    expect("claimed storage retains ownership until release",
        atomic_read(&sample_reads)==DLA_WORKER_BUFFER_SLOTS && result.buffer[31]==1);
    worker_preserve_unconsumed(f.devc,&result);
    dla_worker_release_result(f.devc->worker,TRUE);
    expect("one released credit permits exactly one wrapped read",
        wait_queued(f.devc->worker,DLA_WORKER_BUFFER_SLOTS));
    expect("wrapped producer is bounded",atomic_read(&sample_reads)==DLA_WORKER_BUFFER_SLOTS+1);
    expect("Stop wakes a full deep ring",dev_acquisition_stop(f.sdi)==SR_OK);
    expect("native worker exits before queued results retire",
        WaitForSingleObject(f.devc->worker->thread,1000)==WAIT_OBJECT_0);
    for (unsigned int id=2;id<=DLA_WORKER_BUFFER_SLOTS+1;id++) {
        expect("FIFO identities survive wrap and Stop",dla_worker_take_result(f.devc->worker,&result) &&
            result.read_id==id && result.count==32 && result.buffer[0]==id && result.buffer[31]==id);
        worker_preserve_unconsumed(f.devc,&result);
        dla_worker_release_result(f.devc->worker,FALSE);
    }
    inspect_end=TRUE;
    expect("all returned bytes are reconciled once before END",
        pump_until(&f,NULL,TRUE) && f.devc->worker_unconsumed_bytes==32*(DLA_WORKER_BUFFER_SLOTS+1) &&
        ends==1 && delivered==0 && end_guard_ok && !f.devc->worker_ready);
    fixture_clear(&f);
    printf("Ring audit: %u checks, %u failures, slots=%u.\n",checks,failures,DLA_WORKER_BUFFER_SLOTS);
    return failures ? 1 : 0;
}
