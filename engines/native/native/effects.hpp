#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <sstream>
#include <string>
#include <cstdlib>

inline std::array<int,9> rainbow_frame(double seconds){
    constexpr double pi=3.141592653589793;
    double peak=136+24*std::sin(pi*seconds),phase=std::fmod(seconds,6.0)/6.0;
    std::array<int,9> result{};
    for(int zone=0;zone<3;++zone){
        double hue=std::fmod(phase+zone/3.0,1.0)*6;
        int sector=static_cast<int>(hue);double x=1-std::abs(std::fmod(hue,2.0)-1);
        const double colors[6][3]={{1,x,0},{x,1,0},{0,1,x},{0,x,1},{x,0,1},{1,0,x}};
        for(int color=0;color<3;++color)result[zone*3+color]=std::lround(colors[sector%6][color]*peak);
    }return result;
}
inline bool valid_indicator_style(const std::string &style){
    for(const char *name:{"rainbow","red","blue","green","purple","white","off",
            "pulse-red","pulse-blue","pulse-green","pulse-purple","pulse-white"})
        if(style==name)return true;
    return false;
}
inline std::string indicator_environment_style(){
    const char *value=std::getenv("ALISA_LED_STYLE");
    return value&&valid_indicator_style(value)?value:"rainbow";
}
inline std::string indicator_frame(bool speaking,double seconds,const std::string &style=indicator_environment_style()){
    auto values=speaking?rainbow_frame(seconds):std::array<int,9>{};
    if(speaking&&style!="rainbow"){
        values={};std::string name(style);bool pulse=name.rfind("pulse-",0)==0;
        if(pulse)name=name.substr(6);
        int color[3]{};
        if(name=="red")color[0]=160;else if(name=="green")color[1]=160;
        else if(name=="blue")color[2]=160;else if(name=="purple")color[0]=color[2]=120;
        else if(name=="white")color[0]=color[1]=color[2]=100;
        if(name!="off")for(int zone=0;zone<3;++zone)for(int k=0;k<3;++k)
            values[zone*3+k]=std::lround(color[k]*(pulse?0.75+0.25*std::sin(3.141592653589793*seconds):1.0));
    }
    std::ostringstream command;command<<"F";for(int value:values)command<<' '<<value;command<<'\n';return command.str();
}
inline void mode_cue_block(bool opening,int block,short *samples){
    constexpr double pi=3.141592653589793;
    double hz=(block<5)==opening?587:880;
    for(int i=0;i<960;++i){
        double t=(block%5*960+i)/48000.0;
        double envelope=std::clamp(std::min(t/.008,(.1-t)/.015),0.0,1.0);
        short value=static_cast<short>(2600*envelope*std::sin(2*pi*hz*t));
        samples[i*2]=samples[i*2+1]=value;
    }
}
