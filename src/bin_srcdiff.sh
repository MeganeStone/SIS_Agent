#!/bin/bash

# get text size
rm -Rf hex_size.txt
for file in $(ls -1 ./bin); do
  echo "readelf -S ./bin/${file}"
  if [ ${file} == "ecall_agent" -o ${file} == "icb_service" ]; then
    tmpvalue=0x`readelf -S ./bin/${file} | sed -n '23p' | awk '{ print $1 }'`
  else
    tmpvalue=0x`readelf -S ./bin/${file} | sed -n '21p' | awk '{ print $1 }'`
  fi

  echo "${file}=${tmpvalue}" >> hex_size.txt
done

# convert to hex
rm -Rf bin_size.txt
for size in `cat hex_size.txt`; do
  name=`echo $size | awk -F'=' '{ print $1 }'`
  value=`echo $size | awk -F'=' '{ print $2 }'`
  ret=`printf %d ${value}`

  echo ${name} ${ret} >> bin_size.txt
done

