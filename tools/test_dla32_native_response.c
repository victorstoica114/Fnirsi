/* SPDX-License-Identifier: GPL-3.0-only */
#define main inherited_fixture_main
#define dla_wch_native_sample_read inherited_native_sample_read
#include "../artifacts/dla32-wch-worker-v10-tests/test_dla32_worker_v10_unit.c"
#undef dla_wch_native_sample_read
#undef main

static unsigned int response_kind,response_calls;
static BOOL WINAPI response_ioctl(HANDLE handle,DWORD code,LPVOID input,DWORD input_size,
        LPVOID output,DWORD output_size,LPDWORD returned,LPOVERLAPPED operation)
{
    ULONG *header=output;ULONG requested=header[1];
    (void)handle;(void)code;(void)input;(void)input_size;(void)output_size;(void)operation;
    response_calls++;header[0]=0;header[1]=32;*returned=40;
    memset((uint8_t *)output+8,0x5a,32);SetLastError(ERROR_SUCCESS);
    switch(response_kind) {
    case 1:*returned=7;break;
    case 2:*returned=requested+9;break;
    case 3:header[1]=requested+1;break;
    case 4:*returned=41;break;
    case 5:header[0]=0xc0000001;break;
    case 6:SetLastError(ERROR_TIMEOUT);return FALSE;
    case 7:SetLastError(ERROR_DEVICE_NOT_CONNECTED);return FALSE;
    }
    return TRUE;
}
#define DeviceIoControl response_ioctl
#include "dla32_native_sample_read.h"
#undef DeviceIoControl

int main(void)
{
    struct fixture f;uint8_t bytes[72];int actual,status;unsigned int before;
    main_thread=GetCurrentThreadId();fixture_init(&f,FALSE);
    response_kind=0;
    expect("native valid response preserves payload after one kernel header",dla_wch_native_sample_read(&f.devc->wch,bytes,64,&actual,1000)==LIBUSB_SUCCESS &&
        actual==32 && bytes[8]==0x5a && bytes[39]==0x5a);
    for (response_kind=1;response_kind<=5;response_kind++) {
        status=dla_wch_native_sample_read(&f.devc->wch,bytes,64,&actual,1000);
        expect("malformed native size/count/status exposes no sample bytes",status==LIBUSB_ERROR_OVERFLOW && actual==0);
    }
    response_kind=6;
    expect("native timeout does not trust a partially changed output",dla_wch_native_sample_read(&f.devc->wch,bytes,64,&actual,1000)==LIBUSB_ERROR_TIMEOUT && actual==0);
    response_kind=7;
    expect("native OS loss preserves device-loss identity",dla_wch_native_sample_read(&f.devc->wch,bytes,64,&actual,1000)==LIBUSB_ERROR_NO_DEVICE &&
        actual==0 && f.devc->wch.last_operation_error==ERROR_DEVICE_NOT_CONNECTED);
    before=response_calls;f.devc->wch.native_handle=NULL;
    expect("missing native handle performs no IOCTL",dla_wch_native_sample_read(&f.devc->wch,bytes,64,&actual,1000)==LIBUSB_ERROR_NO_DEVICE &&
        actual==0 && response_calls==before);
    fixture_clear(&f);
    printf("Native response audit: %u checks, %u failures.\n",checks,failures);
    return failures ? 1 : 0;
}
