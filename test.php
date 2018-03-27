#!/usr/bin/php
<?php

function _dump_old($text) {
    $retval = '';
    for($i = 0; $i < strlen($text); ++$i) {
        $retval .= ':'.dechex(ord($text[$i]));
    }

    return $retval;
}

function _dump($string){
    $hex = '';
    for ($i=0; $i<strlen($string); $i++){
        $ord = ord($string[$i]);
        $hexCode = dechex($ord);
        $hex .= '\x' . substr('0'.$hexCode, -2);
    }
    return $hex;
}
  

function mirrorBits($k) {
    $arr = unpack('c*', $k);
    $ret = '';
    $cnt = count($arr);
    if($cnt > 8){
        $cnt = 8;
    }

    for($i=1; $i<=$cnt; $i++){
        $s = $arr[$i];
        $s = (($s >> 1) & 0x55) | (($s << 1) & 0xaa);
        $s = (($s >> 2) & 0x33) | (($s << 2) & 0xcc);
        $s = (($s >> 4) & 0x0f) | (($s << 4) & 0xf0);
        $ret = $ret . chr($s);
    }
    return $ret;
}

$passwd="kaka80\0\0";
$data = "1234567890123456";

$iv = mcrypt_create_iv(mcrypt_get_iv_size (MCRYPT_DES, MCRYPT_MODE_ECB), MCRYPT_RAND);
$crypted = mcrypt_encrypt(MCRYPT_DES, mirrorBits($passwd), $data, MCRYPT_MODE_ECB, $iv);

echo "Crypted: " . base64_encode($crypted);
echo "\n";
