/* Only the voice client's own sockets are marked. Fail closed on mark errors. */
extern void *dlsym(void *,const char *);
extern char *getenv(const char *);
extern int setsockopt(int,int,int,const void *,unsigned int);
extern int close(int);
extern int *__errno_location(void);
int socket(int family,int type,int protocol){
    int (*original)(int,int,int)=dlsym((void *)-1,"socket");
    if(!original){*__errno_location()=38;return -1;}
    int fd=original(family,type,protocol);const char *required=getenv("ALISA_VPN_REQUIRED");
    if(fd>=0&&required&&required[0]=='1'&&(family==2||family==10)){
        unsigned int mark=0xa150;
        if(setsockopt(fd,1,36,&mark,sizeof(mark))<0){int error=*__errno_location();close(fd);*__errno_location()=error;return -1;}
    }
    return fd;
}
