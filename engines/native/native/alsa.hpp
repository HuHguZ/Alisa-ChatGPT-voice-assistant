#pragma once
#include <dlfcn.h>
#include <stdexcept>

class PcmOutput {
    void *library=nullptr,*pcm=nullptr;
    long (*writei)(void *,const void *,unsigned long)=nullptr;
    int (*recover)(void *,int,int)=nullptr;
    int (*drop)(void *)=nullptr;
    int (*close_pcm)(void *)=nullptr;
    template<class T>T symbol(const char *name){
        auto p=dlsym(library,name);if(!p)throw std::runtime_error("alsa_symbol_missing");
        return reinterpret_cast<T>(p);
    }
public:
    unsigned int underruns=0;
    explicit PcmOutput(const char *device){
      void *sw=nullptr;void (*free_sw)(void *)=nullptr;
      try {
        library=dlopen("libasound.so.2",RTLD_NOW|RTLD_LOCAL);
        if(!library)throw std::runtime_error("alsa_unavailable");
        auto open_pcm=symbol<int(*)(void **,const char *,int,int)>("snd_pcm_open");
        auto params=symbol<int(*)(void *,int,int,unsigned int,unsigned int,int,unsigned int)>("snd_pcm_set_params");
        writei=symbol<decltype(writei)>("snd_pcm_writei");recover=symbol<decltype(recover)>("snd_pcm_recover");
        drop=symbol<decltype(drop)>("snd_pcm_drop");close_pcm=symbol<decltype(close_pcm)>("snd_pcm_close");
        if(open_pcm(&pcm,device,0,0)<0||params(pcm,2,3,2,48000,1,80000)<0)
            throw std::runtime_error("alsa_open_failed");
        unsigned long buffer=0,period=0;
        auto get=symbol<int(*)(void *,unsigned long *,unsigned long *)>("snd_pcm_get_params");
        auto alloc=symbol<int(*)(void **)>("snd_pcm_sw_params_malloc");
        auto current=symbol<int(*)(void *,void *)>("snd_pcm_sw_params_current");
        auto start=symbol<int(*)(void *,void *,unsigned long)>("snd_pcm_sw_params_set_start_threshold");
        auto apply=symbol<int(*)(void *,void *)>("snd_pcm_sw_params");
        free_sw=symbol<void(*)(void *)>("snd_pcm_sw_params_free");
        if(get(pcm,&buffer,&period)<0||alloc(&sw)<0||current(pcm,sw)<0)
            throw std::runtime_error("alsa_setup_failed");
        auto threshold=buffer>period?buffer-period:1;if(threshold>2880)threshold=2880;
        if(start(pcm,sw,threshold)<0||apply(pcm,sw)<0)throw std::runtime_error("alsa_setup_failed");
        free_sw(sw);sw=nullptr;
      }catch(...){if(sw&&free_sw)free_sw(sw);if(pcm&&close_pcm)close_pcm(pcm);pcm=nullptr;if(library)dlclose(library);library=nullptr;throw;}
    }
    void drain(){auto fn=symbol<int(*)(void *)>("snd_pcm_drain");if(fn(pcm)<0)throw std::runtime_error("alsa_drain_failed");}
    void write(const short *samples,unsigned long frames){
        unsigned long at=0;int failures=0;
        while(at<frames){
            long n=writei(pcm,samples+at*2,frames-at);
            if(n<0){if(++failures>2||recover(pcm,static_cast<int>(n),1)<0)throw std::runtime_error("alsa_write_failed");++underruns;continue;}
            if(!n)throw std::runtime_error("alsa_zero_write");at+=static_cast<unsigned long>(n);
        }
    }
    ~PcmOutput(){if(pcm){drop(pcm);close_pcm(pcm);}if(library)dlclose(library);}
};
