/* Validated exact commands, one decimal action + tab + UTF-8 phrase per line. */
static int configured_command(const char *value) {
    const char *path=getenv("ALISA_COMMANDS_FILE");
    if(!path||!*path)return -1;
    int fd=open(path,0);if(fd<0)return 0;
    char data[4097];long n=read(fd,data,4096);close(fd);
    if(n<=0||n>=4096)return 0;data[n]=0;
    char *line=data;
    for(long i=0;i<=n;++i)if(data[i]=='\n'||data[i]==0){
        data[i]=0;
        if(line[0]>='1'&&line[0]<='4'&&line[1]=='\t'&&!strcmp(line+2,value))return line[0]-'0';
        line=data+i+1;
    }
    return 0;
}
