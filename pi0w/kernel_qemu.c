/* POKE OS Pi Zero W — QEMU variant (PL011 UART on QEMU virt)
 *
 * Same POKE protocol as kernel.c, but uses PL011 UART on QEMU virt
 * machine with arm1176 CPU (same ARMv6 as real Pi Zero W).
 *
 * Real hardware: kernel.c (Mini UART / AUX at 0x20215000)
 * QEMU testing:  kernel_qemu.c (PL011 at 0x09000000) — this file
 *
 * QEMU: qemu-system-arm -M virt -cpu arm1176 -kernel poke-pi0w-qemu.bin -nographic
 */

typedef unsigned char u8;
typedef unsigned int u32;

/* PL011 UART on QEMU virt machine */
#define UART_BASE   0x09000000
#define UART_DR     (*(volatile u32 *)(UART_BASE + 0x00))
#define UART_FR     (*(volatile u32 *)(UART_BASE + 0x18))
#define UART_IBRD   (*(volatile u32 *)(UART_BASE + 0x24))
#define UART_FBRD   (*(volatile u32 *)(UART_BASE + 0x28))
#define UART_LCRH   (*(volatile u32 *)(UART_BASE + 0x2C))
#define UART_CR     (*(volatile u32 *)(UART_BASE + 0x30))
#define UART_ICR    (*(volatile u32 *)(UART_BASE + 0x44))

/* UART flag bits */
#define FR_RXFE     (1 << 4)  /* RX FIFO empty */
#define FR_TXFF     (1 << 5)  /* TX FIFO full */

/* UART — no init for QEMU (pre-configured) */
static void uart_init(void) {}

static void uart_putc(char c) {
    while (UART_FR & FR_TXFF) {}
    UART_DR = (u32)c;
}
static void uart_print(const char *s) {
    while (*s) { if (*s == '\n') uart_putc('\r'); uart_putc(*s++); }
}
static int uart_available(void) { return !(UART_FR & FR_RXFE); }
static u8 uart_getc(void) { return UART_DR & 0xFF; }

/* Memory */
static int str_len(const char *s) { int n=0; while(*s++)n++; return n; }
static int str_copy(char *d, const char *s) { int n=0; while(*s){d[n++]=*s++;} return n; }
static void mem_copy(void *d, const void *s, int n) { u8 *dd=d; const u8 *ss=s; while(n-->0)*dd++=*ss++; }
static void mem_set(void *d, u8 v, int n) { u8 *dd=d; while(n-->0)*dd++=v; }
static int mem_cmp(const void *a, const void *b, int n) {
    const u8 *p=a, *q=b; while(n-->0){if(*p!=*q)return *p-*q;p++;q++;} return 0;
}

/* POKE protocol */
#define RX_BUF_SIZE 4096
static u8 rx_buf[RX_BUF_SIZE];
static int rx_pos = 0;

static void send_resp(const u8 *payload, u32 len) {
    uart_putc('R');uart_putc('E');uart_putc('S');uart_putc('P');
    uart_putc(len&0xFF);uart_putc((len>>8)&0xFF);
    uart_putc((len>>16)&0xFF);uart_putc((len>>24)&0xFF);
    for (u32 i=0;i<len;i++) uart_putc(payload[i]);
}
static void send_resp_str(const char *s) { send_resp((const u8*)s, str_len(s)); }

/* Num to string */
static int itoa_dec(u32 val, char *buf) {
    char tmp[12]; int i=0;
    if (val==0){buf[0]='0';return 1;}
    while(val>0){tmp[i++]='0'+(val%10);val/=10;}
    int len=i; for(int j=0;j<len;j++)buf[j]=tmp[len-1-j];
    return len;
}

/* Code execution */
#define CODE_BUF_SIZE 4096
static u8 code_buf[CODE_BUF_SIZE] __attribute__((aligned(4096)));
static char result_buf[256];
static int result_len = 0;

static void handle_exec(const u8 *code, int code_len) {
    if (code_len<=0||code_len>CODE_BUF_SIZE){send_resp_str("error: size");return;}
    mem_copy(code_buf, code, code_len);
    int has_ret=0;
    for(int i=0;i<=code_len-4;i+=4){
        u32 insn=code_buf[i]|(code_buf[i+1]<<8)|(code_buf[i+2]<<16)|(code_buf[i+3]<<24);
        if(insn==0xE12FFF1E||insn==0xE1A0F00E){has_ret=1;break;}
    }
    mem_set(result_buf,0,256); result_len=0;
    u32 ret_r0=0;
    if(has_ret){
        __asm__ volatile("mcr p15,0,%0,c7,c5,0"::"r"(0));
        __asm__ volatile("mcr p15,0,%0,c7,c10,4"::"r"(0));
        __asm__ volatile("mcr p15,0,%0,c7,c5,4"::"r"(0));
        u32(*fn)(char*,int*)=(u32(*)(char*,int*))code_buf;
        ret_r0=fn(result_buf,&result_len);
    }
    char resp[128]; int rlen=0;
    if(!has_ret){rlen=str_copy(resp,"no RET found");}
    else if(result_len>0){mem_copy(resp,result_buf,result_len);rlen=result_len;}
    else{rlen+=str_copy(resp+rlen,"r0=");rlen+=itoa_dec(ret_r0,resp+rlen);}
    send_resp((const u8*)resp,rlen);
}

/* Virtual GPIO */
#define VGPIO_COUNT 11
static u8 vgpio_pins[VGPIO_COUNT];

static void handle_gpio(void) {
    char resp[256]; int n=0;
    n+=str_copy(resp+n,"{\"gpio\":{");
    for(int pin=0;pin<VGPIO_COUNT;pin++){
        if(pin>0)resp[n++]=',';
        resp[n++]='"'; n+=itoa_dec(pin,resp+n); resp[n++]='"'; resp[n++]=':';
        resp[n++]='0'+(vgpio_pins[pin]?1:0);
    }
    n+=str_copy(resp+n,"}}"); resp[n]=0;
    send_resp_str(resp);
}

static void handle_gpos(const u8 *data, int len) {
    if(len<2){send_resp_str("{\"error\":\"need pin+value\"}");return;}
    u8 pin=data[0], val=data[1];
    if(pin>=VGPIO_COUNT){send_resp_str("{\"error\":\"bad pin\"}");return;}
    vgpio_pins[pin]=val?1:0;
    char resp[64]; int n=0;
    n+=str_copy(resp+n,"{\"pin\":"); n+=itoa_dec(pin,resp+n);
    n+=str_copy(resp+n,",\"value\":"); resp[n++]='0'+(val?1:0);
    resp[n++]='}'; resp[n]=0;
    send_resp_str(resp);
}

static void handle_temp(void) {
    static u32 tick=0; tick++;
    u32 temp_x10=350+(tick%30);
    char resp[64]; int n=0;
    n+=str_copy(resp+n,"{\"celsius\":");
    n+=itoa_dec(temp_x10/10,resp+n);
    resp[n++]='.'; resp[n++]='0'+(temp_x10%10);
    n+=str_copy(resp+n,",\"virtual\":true}"); resp[n]=0;
    send_resp_str(resp);
}

/* Handlers */
static void handle_info(void) {
    send_resp_str("{\"status\":\"alive\",\"arch\":\"armv6\",\"chip\":\"bcm2835\",\"kernel\":\"poke-os\",\"bare_metal\":true}");
}

static void process_frame(const u8 *payload, u32 len) {
    if (len < 4) { send_resp_str("error"); return; }
    if (mem_cmp(payload,"PING",4)==0) { send_resp_str("PONG"); }
    else if (mem_cmp(payload,"INFO",4)==0) { handle_info(); }
    else if (mem_cmp(payload,"EXEC",4)==0) { handle_exec(payload+4,len-4); }
    else if (mem_cmp(payload,"GPIO",4)==0) { handle_gpio(); }
    else if (mem_cmp(payload,"GPOS",4)==0) { handle_gpos(payload+4,len-4); }
    else if (mem_cmp(payload,"TEMP",4)==0) { handle_temp(); }
    else { send_resp_str("error: unknown"); }
}

static void poll_protocol(void) {
    while (rx_pos >= 8) {
        if (rx_buf[0]=='P'&&rx_buf[1]=='O'&&rx_buf[2]=='K'&&rx_buf[3]=='E') {
            u32 plen=rx_buf[4]|(rx_buf[5]<<8)|(rx_buf[6]<<16)|(rx_buf[7]<<24);
            if (plen>RX_BUF_SIZE-8){rx_pos=0;break;}
            if (rx_pos>=(int)(8+plen)) {
                process_frame(rx_buf+8,plen);
                int consumed=8+plen;
                int left=rx_pos-consumed;
                if(left>0){for(int i=0;i<left;i++)rx_buf[i]=rx_buf[consumed+i];}
                rx_pos=left;
            } else break;
        } else {
            for(int i=0;i<rx_pos-1;i++)rx_buf[i]=rx_buf[i+1];
            rx_pos--;
        }
    }
}

void kernel_main(void) {
    uart_init();
    uart_print("\nPOKE OS Pi Zero W (QEMU)\n");
    uart_print("poke-pi0w> ");

    /* Main loop */
    while (1) {
        while (uart_available() && rx_pos < RX_BUF_SIZE) {
            rx_buf[rx_pos++] = uart_getc();
        }
        poll_protocol();
    }
}
