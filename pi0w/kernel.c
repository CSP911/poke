/* Debug step 2: LED blink + UART + string functions + main loop
 * Previous: 8 steps all passed
 * Adding: mem functions, send_resp, POKE frame parser, main loop */

typedef unsigned char u8;
typedef unsigned int u32;

#define PERI_BASE   0x20000000
#define GPIO_BASE   (PERI_BASE + 0x200000)
#define GPFSEL1     (*(volatile u32 *)(GPIO_BASE + 0x04))
#define GPFSEL4     (*(volatile u32 *)(GPIO_BASE + 0x10))
#define GPSET1      (*(volatile u32 *)(GPIO_BASE + 0x20))
#define GPCLR1      (*(volatile u32 *)(GPIO_BASE + 0x2C))

#define AUX_BASE    (PERI_BASE + 0x215000)
#define AUX_ENABLES (*(volatile u32 *)(AUX_BASE + 0x04))
#define AUX_MU_IO   (*(volatile u32 *)(AUX_BASE + 0x40))
#define AUX_MU_IER  (*(volatile u32 *)(AUX_BASE + 0x44))
#define AUX_MU_IIR  (*(volatile u32 *)(AUX_BASE + 0x48))
#define AUX_MU_LCR  (*(volatile u32 *)(AUX_BASE + 0x4C))
#define AUX_MU_MCR  (*(volatile u32 *)(AUX_BASE + 0x50))
#define AUX_MU_LSR  (*(volatile u32 *)(AUX_BASE + 0x54))
#define AUX_MU_CNTL (*(volatile u32 *)(AUX_BASE + 0x60))
#define AUX_MU_BAUD (*(volatile u32 *)(AUX_BASE + 0x68))

/* LED */
static void led_setup(void) {
    GPFSEL4 &= ~(7 << 21);
    GPFSEL4 |= (1 << 21);
}
static void led_on(void) { GPSET1 = (1 << (47-32)); }
static void led_off(void) { GPCLR1 = (1 << (47-32)); }
static void delay(int n) { for (volatile int i = 0; i < n; i++) {} }
static void blink(int times) {
    for (int i = 0; i < times; i++) {
        led_on(); delay(500000);
        led_off(); delay(500000);
    }
    delay(2000000);
}

/* UART */
static void uart_init(void) {
    AUX_ENABLES = 1;
    AUX_MU_IER = 0;
    AUX_MU_CNTL = 0;
    AUX_MU_LCR = 3;
    AUX_MU_MCR = 0;
    AUX_MU_IIR = 0xC6;
    AUX_MU_BAUD = 270;
    u32 sel = GPFSEL1;
    sel &= ~(7 << 12); sel |= (2 << 12);
    sel &= ~(7 << 15); sel |= (2 << 15);
    GPFSEL1 = sel;
    AUX_MU_CNTL = 3;
}

static void uart_putc(char c) {
    while (!(AUX_MU_LSR & 0x20)) {}
    AUX_MU_IO = c;
}
static void uart_print(const char *s) {
    while (*s) { if (*s == '\n') uart_putc('\r'); uart_putc(*s++); }
}
static int uart_available(void) { return AUX_MU_LSR & 0x01; }
static u8 uart_getc(void) { return AUX_MU_IO & 0xFF; }

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
    led_setup();
    blink(1); /* alive */

    uart_init();
    blink(2); /* UART OK */

    uart_print("\nPOKE OS Pi Zero W (debug2)\n");
    uart_print("poke-pi0w> ");
    blink(3); /* print OK */

    /* Main loop */
    while (1) {
        while (uart_available() && rx_pos < RX_BUF_SIZE) {
            rx_buf[rx_pos++] = uart_getc();
        }
        poll_protocol();

        /* LED heartbeat every ~2 seconds */
        static int hb = 0;
        if (++hb > 2000000) {
            hb = 0;
            led_on(); delay(50000); led_off();
        }
    }
}
