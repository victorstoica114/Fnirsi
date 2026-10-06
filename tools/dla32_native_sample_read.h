/* SPDX-License-Identifier: GPL-3.0-only */
/* Native request layout verified against the installed SDK and kernel code.
 * Uses the SDK-owned synchronous handle; no binding/firmware changes.
 * DeviceIoControl contract: https://learn.microsoft.com/windows/win32/api/ioapiset/nf-ioapiset-deviceiocontrol
 */
#if DLA_WCH_NATIVE_READ
static int dla_wch_native_sample_read(struct dla_wch *wch,uint8_t *storage,
        ULONG length,int *actual,unsigned int timeout)
{
    ULONG *header=(ULONG *)storage;
    DWORD returned=0,error;
    BOOL ok;
    *actual=0;
    if (!wch->opened || !wch->native_handle || wch->native_handle==INVALID_HANDLE_VALUE)
        return LIBUSB_ERROR_NO_DEVICE;
    if (!dla_wch_operation_timeout(wch,timeout,"native read"))return LIBUSB_ERROR_IO;
    header[0]=0x10000;header[1]=length;
    SetLastError(ERROR_SUCCESS);
    ok=DeviceIoControl(wch->native_handle,0x223cdc,storage,8,storage,length+8,&returned,NULL);
    error=GetLastError();wch->last_operation_error=error;
    if (!ok) {
        /* No trusted failure output is passed to the decoder. */
        if (error==ERROR_SEM_TIMEOUT || error==ERROR_TIMEOUT || error==WAIT_TIMEOUT)return LIBUSB_ERROR_TIMEOUT;
        if (error==ERROR_DEVICE_NOT_CONNECTED || error==ERROR_NO_SUCH_DEVICE)return LIBUSB_ERROR_NO_DEVICE;
        return LIBUSB_ERROR_IO;
    }
    if (returned<8 || returned>length+8 || header[1]>length || header[1]+8!=returned || header[0]!=0) {
        dla_wch_log(wch,SR_LOG_ERR,"Native read invalid kernel response: returned %lu, status %08lx, count %lu, requested %lu.",
            returned,header[0],header[1],length);
        return LIBUSB_ERROR_OVERFLOW;
    }
    *actual=(int)header[1];return LIBUSB_SUCCESS;
}
#endif
