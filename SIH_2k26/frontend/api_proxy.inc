proxy_pass http://api:8000;
proxy_http_version 1.1;
proxy_set_header Host $host;
# Exactly ONE entry, the address nginx saw: a client-supplied X-Forwarded-For is discarded, so TRUSTED_PROXY_COUNT=1 on the API is correct and unspoofable.
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
proxy_set_header X-Request-ID $request_id;
proxy_connect_timeout 5s;
proxy_read_timeout 90s;
proxy_send_timeout 90s;
proxy_request_buffering off;
