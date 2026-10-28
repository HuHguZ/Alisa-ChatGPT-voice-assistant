#include "settings.hpp"
#include "web_auth.hpp"
#include "http_server.hpp"
#include <cassert>
#include <iostream>

std::string token(double expiry){
    auto text=json{{"exp",expiry}}.dump();
    std::string encoded(4*((text.size()+2)/3),'\0');
    EVP_EncodeBlock(reinterpret_cast<unsigned char *>(encoded.data()),
        reinterpret_cast<const unsigned char *>(text.data()),text.size());
    while(encoded.back()=='=')encoded.pop_back();
    return "e30."+encoded+".signature";
}

int main(int argc,char **argv){
    fs::path dir=argv[1];fs::create_directories(dir);
    if(argc==4){
        ControlServer server(std::stoi(argv[3]),dir,[]{return json{{"ok",true}};},
            [](const json &request){return json{{"action",request.at("action")}};});
        server.run();return 0;
    }
    Settings settings(dir/"settings.json");
    auto a=settings.select("open"),b=settings.select("new");
    assert(a["id"]!=b["id"]);
    settings.update(a["id"],{{"conversation_id","cloud-a"},{"parent_message_id","parent-a"},{"voice_session_id","voice-a"}},"account-a");
    assert(settings.select("previous")["id"]==a["id"]);
    assert(settings.select("previous")["id"]==b["id"]);
    settings.voice("arbor");settings.rename(a["id"],"Тест A");
    Settings reloaded(dir/"settings.json");
    assert(reloaded.view()["voice"]=="fathom");
    auto resumed=reloaded.select("select",a["id"]);
    assert(resumed["parent_message_id"]=="parent-a"&&resumed["voice_session_id"]=="voice-a");
    assert(resumed["account_id"]=="account-a"&&resumed["confirmed"]==true);
    bool rejected=false;try{settings.voice("unknown");}catch(...){rejected=true;}assert(rejected);
    auto now=std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
    assert(web_auth_current({{"access_token",token(now+3600)}}));
    assert(!web_auth_current({{"access_token",token(now+60)}}));
    assert(!web_auth_current({{"access_token","malformed"}}));
    assert(!web_auth_current({{"access_token","a.====.b"}}));
    std::cout<<"settings persistence, separate chats, voice aliases, auth expiry: passed\n";
}
