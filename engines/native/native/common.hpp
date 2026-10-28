#pragma once
#include <nlohmann/json.hpp>
#include <openssl/rand.h>
#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <fcntl.h>
#include <unistd.h>

using json=nlohmann::json;
using Clock=std::chrono::steady_clock;
namespace fs=std::filesystem;
inline std::atomic<bool> stopping{false};
inline const fs::path runtime_directory="/tmp/alisa-standalone";
inline std::string random_id() {
    unsigned char b[16];if(RAND_bytes(b,sizeof(b))!=1)throw std::runtime_error("random_failed");
    b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;
    static const char hex[]="0123456789abcdef";std::string s;
    for(int i=0;i<16;++i){if(i==4||i==6||i==8||i==10)s+='-';s+=hex[b[i]>>4];s+=hex[b[i]&15];}
    return s;
}
inline json read_json(const fs::path &path) {
    std::error_code ec;auto size=fs::file_size(path,ec);
    if(ec||size>1024*1024)throw std::runtime_error("state_unavailable");
    std::ifstream f(path);json value;f>>value;return value;
}
inline void write_json(const fs::path &path,const json &value) {
    fs::create_directories(path.parent_path());
    auto temporary=path.string()+".new";
    int fd=open(temporary.c_str(),O_WRONLY|O_CREAT|O_TRUNC|O_CLOEXEC,0600);
    if(fd<0)throw std::runtime_error("state_write_failed");
    std::string text=value.dump(2)+"\n";size_t offset=0;
    while(offset<text.size()){
        auto n=write(fd,text.data()+offset,text.size()-offset);
        if(n<=0){close(fd);unlink(temporary.c_str());throw std::runtime_error("state_write_failed");}
        offset+=n;
    }
    int result=fsync(fd);close(fd);
    if(result<0||rename(temporary.c_str(),path.c_str())<0)throw std::runtime_error("state_commit_failed");
    int dir=open(path.parent_path().c_str(),O_RDONLY|O_DIRECTORY|O_CLOEXEC);
    if(dir>=0){fsync(dir);close(dir);}
}
inline long rss_kib() {
    std::ifstream f("/proc/self/status");std::string line;
    while(std::getline(f,line))if(line.rfind("VmRSS:",0)==0)return std::stol(line.substr(6));
    return 0;
}
struct SocketOwner {
    int fd=-1;std::string path;
    ~SocketOwner(){if(fd>=0)close(fd);if(!path.empty())unlink(path.c_str());}
};
