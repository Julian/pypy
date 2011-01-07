/* This optional file only works for GCC on an x86-64.
 */

#define READ_TIMESTAMP(val) do {                        \
    unsigned long _rax, _rdx;                           \
    asm volatile("rdtsc" : "=rax"(_rax), "=rdx"(_rdx)); \
    val = (_rdx << 32) | _rax;                          \
} while (0)
