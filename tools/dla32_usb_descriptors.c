#include <libusb.h>
#include <stdio.h>
int main(void){libusb_context *ctx=0;libusb_device **list=0;struct libusb_device_descriptor d;struct libusb_config_descriptor *c=0;ssize_t count;int found=0;
if(libusb_init(&ctx))return 1;count=libusb_get_device_list(ctx,&list);
for(ssize_t i=0;i<count;i++){if(libusb_get_device_descriptor(list[i],&d)||d.idVendor!=0x1a86||d.idProduct!=0x5537)continue;found++;printf("DLA bus=%u address=%u speed=%d\n",libusb_get_bus_number(list[i]),libusb_get_device_address(list[i]),libusb_get_device_speed(list[i]));
if(libusb_get_active_config_descriptor(list[i],&c))continue;for(int j=0;j<c->bNumInterfaces;j++)for(int k=0;k<c->interface[j].num_altsetting;k++){const struct libusb_interface_descriptor *a=&c->interface[j].altsetting[k];printf("interface %u alt %u endpoints %u\n",a->bInterfaceNumber,a->bAlternateSetting,a->bNumEndpoints);for(int n=0;n<a->bNumEndpoints;n++){const struct libusb_endpoint_descriptor *e=&a->endpoint[n];printf("endpoint=0x%02x attributes=0x%02x maxpacket=%u\n",e->bEndpointAddress,e->bmAttributes,e->wMaxPacketSize);}}
libusb_free_config_descriptor(c);c=0;}libusb_free_device_list(list,1);libusb_exit(ctx);return found==1?0:1;}
