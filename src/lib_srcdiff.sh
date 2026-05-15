#!/bin/bash

# get text size
rm -Rf hex_size.txt
for file in $(ls -1 ./lib/*.so); do
  echo "readelf -S ./lib/${file}"
  tmpvalue=0x`readelf -S ./lib/${file} | sed -n '29p' | awk '{ print $1 }'`
  echo "${file}=${tmpvalue}" >> hex_size.txt
done

# convert to hex
rm -Rf lib_size.txt
for size in `cat hex_size.txt`; do
  name=`echo $size | awk -F'=' '{ print $1 }'`
  value=`echo $size | awk -F'=' '{ print $2 }'`
  ret=`printf %d ${value}`

  echo ${name} ${ret} >> lib_size.txt
done

